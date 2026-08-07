"""ImageIngestionPipeline — Extract -> Filter -> Dedup -> Normalise -> Store ->
Embed -> Upsert (+ optional text stubs).

Runs AFTER text chunking so it can link each asset to the parent chunk the
chunker already produced, and so table crops can be matched to their table chunk.
"""

import hashlib
from collections import Counter
from pathlib import Path
from typing import Any

from ..config.settings import Settings
from ..embeddings.base import BaseEmbedder
from ..embeddings.multimodal.base import BaseMultimodalEmbedder
from ..extraction.base import ExtractionResult
from ..images.captions import CaptionResolver
from ..images.dedup import Deduper, dhash
from ..images.factory import get_image_extractor
from ..images.filters import ImageFilter
from ..images.preprocess import make_thumbnail, normalize
from ..images.store import BaseAssetStore
from ..models.asset import (
    ContentType,
    EmbeddedImage,
    ImageAsset,
    ImageIngestionResult,
    ImageRole,
    Modality,
)
from ..models.chunk import Chunk, ChunkMetadata, EmbeddedChunk
from ..utils.hashing import make_chunk_id
from ..utils.logging import get_logger
from ..vectordb.base import BaseVectorDB
from ..vectordb.image_store import BaseImageStore

logger = get_logger(__name__)

_MIN_STUB_CHARS = 40


class ImageIngestionPipeline:
    def __init__(
        self,
        settings: Settings,
        multimodal_embedder: BaseMultimodalEmbedder | None,
        text_embedder: BaseEmbedder,
        image_store: BaseImageStore | None,
        vectordb: BaseVectorDB,
        asset_store: BaseAssetStore,
    ) -> None:
        self._settings = settings
        self._embedder = multimodal_embedder
        self._text_embedder = text_embedder
        self._image_store = image_store
        self._vectordb = vectordb
        self._asset_store = asset_store
        self._config = settings.multimodal.extraction
        self._extractor = get_image_extractor(self._config, settings.chunking)
        self._filter = ImageFilter(self._config)
        self._captions = CaptionResolver(self._config)

    async def ingest_images(
        self,
        file_path: Path,
        extraction: ExtractionResult,
        chunks: list[Chunk],
        base_metadata: dict[str, Any],
    ) -> ImageIngestionResult:
        if not self._config.enabled:
            return ImageIngestionResult()

        stats: Counter = Counter()

        # ── 1. Extract ────────────────────────────────────────────────
        raws = await self._extractor.extract_images(file_path)
        stats["total_extracted"] = len(raws)

        # ── 2. Filter, normalise, dedup ───────────────────────────────
        deduper = Deduper(self._config)
        assets: list[ImageAsset] = []
        pils: list[Any] = []

        for raw in raws:
            verdict = self._filter.evaluate(raw)
            if not verdict.keep:
                stats[f"rejected_{verdict.reason}"] += 1
                continue

            try:
                norm_bytes, pil = normalize(raw.data, self._settings.multimodal.storage)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Image normalisation failed", error=str(exc))
                stats["rejected_undecodable"] += 1
                continue

            sha = hashlib.sha256(norm_bytes).hexdigest()
            phash = dhash(pil)
            is_dup, _ = deduper.is_duplicate(sha, phash)
            if is_dup:
                stats["rejected_duplicate"] += 1
                continue

            # ── 3. Context ────────────────────────────────────────────
            ctx = self._captions.resolve(raw, extraction, chunks)

            # ── 4. Store ──────────────────────────────────────────────
            stored = self._asset_store.put(
                norm_bytes,
                make_thumbnail(
                    pil,
                    self._settings.multimodal.storage.thumbnail_side_px,
                    self._settings.multimodal.storage,
                ),
            )
            deduper.remember(sha, phash, stored.asset_id)

            # A table crop links to its table CHUNK, not to a prose chunk.
            parent_chunk_id = ctx.parent_chunk_id
            if raw.role is ImageRole.TABLE_IMAGE:
                table_chunk = self._match_table_chunk(chunks, raw.page_number, ctx.caption)
                if table_chunk is not None:
                    parent_chunk_id = table_chunk.id
                stats["table_crops"] += 1

            assets.append(
                ImageAsset(
                    id=stored.asset_id,
                    content_sha256=sha,
                    phash=phash,
                    document_name=base_metadata["document_name"],
                    document_type=base_metadata.get("document_type", "other"),
                    page_number=raw.page_number,
                    bbox=raw.bbox,
                    image_index_on_page=raw.index_on_page,
                    width=pil.width,
                    height=pil.height,
                    image_format=self._settings.multimodal.storage.image_format,
                    byte_size=len(norm_bytes),
                    role=raw.role,
                    uri=stored.uri,
                    storage_path=stored.path,
                    thumbnail_uri=stored.thumb_uri,
                    caption=ctx.caption,
                    surrounding_text=ctx.surrounding_text,
                    nearest_heading=ctx.nearest_heading,
                    parent_chunk_id=parent_chunk_id,
                    product_applicability=base_metadata.get("product_applicability", []),
                    effective_date=base_metadata.get("effective_date", ""),
                    space_id=self._embedder.space.space_id if self._embedder else "",
                )
            )
            pils.append(pil)

        if not assets:
            return ImageIngestionResult(stats=dict(stats))

        # ── 5. Embed + 6. Upsert (skipped when no multimodal model) ───
        indexed = 0
        if self._embedder is not None and self._image_store is not None:
            output = await self._embedder.embed_images(pils)
            indexed = await self._image_store.upsert_images(
                [
                    EmbeddedImage(asset=a, dense_vector=v, space_id=a.space_id)
                    for a, v in zip(assets, output.dense_vectors)
                ]
            )
        else:
            # Phase 2b: assets + stubs only, no encoder installed yet.
            logger.info("No multimodal embedder; indexing stubs only", assets=len(assets))

        # ── 7. Image-stub chunks into the EXISTING text collection ────
        stubs = 0
        if self._config.write_image_stub_chunks:
            stubs = await self._upsert_stubs(assets, base_metadata)

        table_crops = sum(1 for a in assets if a.role is ImageRole.TABLE_IMAGE)
        figures = len(assets) - table_crops
        logger.info(
            "Image ingestion complete",
            file=str(file_path),
            image_vectors=indexed,  # = figures + table_crops
            figures=figures,
            table_crops=table_crops,
            stubs=stubs,
            **{k: v for k, v in stats.items() if k.startswith("rejected_")},
        )
        return ImageIngestionResult(
            images_indexed=indexed,
            figures=figures,
            table_crops=table_crops,
            stubs_created=stubs,
            stats=dict(stats),
        )

    # ── Helpers ───────────────────────────────────────────────────────
    @staticmethod
    def _match_table_chunk(
        chunks: list[Chunk], page: int | None, caption: str
    ) -> Chunk | None:
        """Find the table chunk a rendered crop belongs to."""
        tables = [c for c in chunks if c.metadata.content_type == ContentType.TABLE.value]
        if not tables:
            return None
        if page is not None:
            same_page = [c for c in tables if c.metadata.source_page == page]
            if same_page:
                return same_page[0]
        if caption:
            for c in tables:
                if caption[:40].lower() in c.text.lower():
                    return c
        return tables[0] if len(tables) == 1 else None

    async def _upsert_stubs(
        self, assets: list[ImageAsset], base_metadata: dict[str, Any]
    ) -> int:
        """Text-searchable descriptions of images, embedded with the TEXT model.

        Gives images reachability through the legacy hybrid path and a citable
        handle for the caption-only fallback generation path.
        """
        stubs: list[Chunk] = []
        for a in assets:
            text = (
                f"[Figure] {a.caption}\n"
                f"Section: {a.nearest_heading}\n"
                f"Page {a.page_number} of {a.document_name}\n"
                f"Context: {a.surrounding_text}"
            ).strip()
            if len(text) < _MIN_STUB_CHARS:
                continue  # Not enough signal to be worth a vector.
            stubs.append(
                Chunk(
                    id=make_chunk_id(a.document_name, f"imgstub_{a.id}"),
                    text=text,
                    metadata=ChunkMetadata(
                        **{
                            **base_metadata,
                            "clause_reference": f"figure_p{a.page_number}",
                            "section_heading": a.nearest_heading,
                            "source_page": a.page_number,
                            "parent_chunk_id": a.parent_chunk_id,
                            "modality": Modality.IMAGE_STUB.value,
                            "content_type": ContentType.IMAGE_STUB.value,
                            "asset_id": a.id,
                        }
                    ),
                )
            )
        if not stubs:
            return 0

        output = await self._text_embedder.embed_documents([c.text for c in stubs])
        embedded = [
            EmbeddedChunk(
                chunk=c,
                dense_vector=output.dense_vectors[i],
                sparse_indices=output.sparse_indices[i] if output.sparse_indices else [],
                sparse_values=output.sparse_values[i] if output.sparse_values else [],
            )
            for i, c in enumerate(stubs)
        ]
        return await self._vectordb.upsert(embedded)
