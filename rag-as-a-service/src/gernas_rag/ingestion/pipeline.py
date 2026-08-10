"""IngestionPipeline — orchestrates Extract → Chunk → Embed → Upsert."""

import asyncio
from pathlib import Path

from ..chunking.factory import get_chunker
from ..config.settings import Settings
from ..embeddings.base import BaseEmbedder
from ..enrichment.base import EnrichmentInput
from ..enrichment.factory import get_enricher
from ..extraction.base import ElementType, ExtractedElement, ExtractionResult
from ..extraction.factory import get_extractor
from ..models.chunk import Chunk, EmbeddedChunk
from ..models.ingestion import IngestionResult, IngestionStatus
from ..storage.artifact_store import get_artifact_store
from ..utils.logging import get_logger
from ..vectordb.base import BaseVectorDB
from .metadata import MetadataExtractor

logger = get_logger(__name__)

_MEDIA_TYPES = (ElementType.FIGURE, ElementType.TABLE)


def _context_text(el: ExtractedElement) -> str:
    """Textual context to hand the VLM alongside the crop.

    A figure/table title very often sits in its own layout block — a separate
    "section_header" line above the plot, or a caption below it — outside the
    picture/table's own bounding box that ``get_image()`` crops. Anything
    printed only there (a date range, a figure number, the series it covers)
    is invisible to a model that only sees the crop, no matter how well it
    reads pixels. Docling's own caption link (see ``DoclingExtractor._caption_of``)
    is the reliable half of this; the nearest heading is a fallback for when no
    caption link exists. Both are handed over as text, not asked to be
    transcribed as if printed on the image.
    """
    heading = el.metadata.get("nearest_heading", "")
    caption = el.metadata.get("docling_caption", "")
    if heading and caption:
        return f"{heading}. Caption: {caption}"
    return caption or heading


class IngestionPipeline:
    """Orchestrates the full ingestion flow:

    ``Document file → Extract → Chunk → Embed → Upsert to VectorDB``

    Designed for async operation. Embedding batches run in a thread pool. Supports
    concurrent document processing via ``asyncio.gather`` with bounded concurrency.
    Idempotent — re-running on the same document updates existing chunks.
    """

    def __init__(self, settings: Settings, embedder: BaseEmbedder, vectordb: BaseVectorDB) -> None:
        self._settings = settings
        self._embedder = embedder
        self._vectordb = vectordb
        self._chunker = get_chunker(settings.chunking)
        self._metadata = MetadataExtractor()
        # Multimodal enrichment (image-as-text). Disabled by default; when off,
        # the extractor never rasterises images and the Enrich stage is skipped.
        self._enrichment_enabled = settings.enrichment.enabled
        self._enricher = None
        self._artifact_store = None
        if self._enrichment_enabled:
            self._enricher = get_enricher(settings.enrichment, settings.llm)
            self._artifact_store = get_artifact_store(settings.artifact_store)
        # Extractor is shared across all files — avoids reloading Docling weights per document.
        self._extractor = get_extractor(
            settings.chunking, Path("placeholder.pdf"), settings.enrichment
        )

    async def ingest_file(
        self,
        file_path: Path,
        document_type: str,
        product_applicability: list[str] | None = None,
        effective_date: str = "",
        original_name: str | None = None,
    ) -> IngestionResult:
        """Ingest a single document file end-to-end.

        ``original_name`` is the user-facing filename (used for ``document_name``
        and type inference) when ``file_path`` points at a staged temp file.
        """
        logger.info("Starting ingestion", file=str(file_path))
        try:
            # Step 1: Extract
            extraction = await self._extractor.extract(file_path)

            # Step 2: Enrich media elements (image-as-text). No-op when disabled.
            if self._enrichment_enabled:
                extraction = await self._enrich(extraction)

            # Step 3: Chunk with metadata
            base_metadata = self._metadata.build_base_metadata(
                file_path,
                document_type,
                product_applicability,
                effective_date,
                raw_text=extraction.raw_markdown,
                original_name=original_name,
            )
            chunks = self._chunker.chunk(extraction, base_metadata)

            # Step 4: Embed in batches
            embedded_chunks = await self._embed_chunks_in_batches(chunks)

            # Step 5: Upsert
            count = await self._vectordb.upsert(embedded_chunks)
            logger.info("Ingestion complete", file=str(file_path), chunks_upserted=count)
            return IngestionResult(
                file_path=str(file_path),
                chunks_created=count,
                status=IngestionStatus.SUCCESS.value,
            )
        except Exception as exc:  # Never crash the pipeline — log and report.
            logger.error("Ingestion failed", file=str(file_path), error=str(exc))
            return IngestionResult(
                file_path=str(file_path),
                chunks_created=0,
                status=IngestionStatus.ERROR.value,
                error=str(exc),
            )

    async def ingest_directory(
        self,
        directory: Path,
        document_type: str,
        max_concurrent: int | None = None,
    ) -> list[IngestionResult]:
        """Ingest all documents in a directory. Processes ``max_concurrent`` in parallel."""
        extensions = self._settings.ingestion.supported_extensions
        files: list[Path] = []
        for ext in extensions:
            files.extend(directory.glob(f"**/*{ext}"))
        limit = max_concurrent or self._settings.ingestion.max_concurrent_documents
        semaphore = asyncio.Semaphore(limit)

        async def ingest_with_sem(f: Path) -> IngestionResult:
            async with semaphore:
                return await self.ingest_file(f, document_type)

        return await asyncio.gather(*[ingest_with_sem(f) for f in files])

    async def _enrich(self, extraction: ExtractionResult) -> ExtractionResult:
        """Enrich media elements in place: store each image, caption it with the
        VLM, and write ``caption``/``artifact_ref``/``enrichment_model`` back onto
        the element. ``raw_markdown`` is untouched — the chunker turns each enriched
        element into one atomic media chunk (design §8, §9). Fail-soft throughout:
        a VLM failure keeps the element's source text and never breaks ingestion.
        """
        candidates = [
            el
            for el in extraction.elements
            if el.element_type in _MEDIA_TYPES
            and el.image_bytes is not None
            and len(el.image_bytes) >= self._settings.enrichment.min_image_bytes
        ]
        if not candidates or self._enricher is None or self._artifact_store is None:
            return extraction

        semaphore = asyncio.Semaphore(self._settings.enrichment.max_concurrent)

        async def _process(el: ExtractedElement) -> None:
            async with semaphore:
                assert el.image_bytes is not None
                try:
                    ref = await self._artifact_store.put_bytes(el.image_bytes, "image/png")
                except Exception as exc:  # noqa: BLE001 — storage failure = skip this element.
                    logger.warning("Artifact store failed; skipping element", error=str(exc))
                    return
                result = await self._enricher.enrich(
                    EnrichmentInput(
                        image_bytes=el.image_bytes,
                        mime_type="image/png",
                        element_type=el.element_type.value,
                        context_text=_context_text(el),
                        confidence=el.metadata.get("table_confidence"),
                    )
                )
                if result.ok and result.caption_text:
                    el.text = result.caption_text  # Full caption becomes the chunk text.
                else:
                    # VLM failed and the element has no other text (figures carry
                    # none natively) — artifact_ref still gets set below so the
                    # image stays resolvable, but with empty text the chunker's
                    # `_build_media_chunks` will drop the element entirely. Surface
                    # that now instead of leaving a stored-but-unindexed image for
                    # stage2a to discover later as an ORPHAN_ARTIFACT.
                    if not el.text.strip():
                        logger.warning(
                            "VLM enrichment failed with no fallback text; this "
                            "element will be dropped during chunking",
                            artifact_ref=ref,
                            element_type=el.element_type.value,
                            page=el.page_number,
                        )
                el.metadata["artifact_ref"] = ref  # Marks this as an enriched media chunk.
                el.metadata["enrichment_model"] = result.model_name  # None on degrade.
                # Free the raw bytes now they are persisted (kept only for the chunk ref).
                el.image_bytes = None

        await asyncio.gather(*(_process(el) for el in candidates))
        enriched = sum(1 for el in candidates if el.metadata.get("enrichment_model"))
        logger.info("Enrichment complete", media_elements=len(candidates), enriched=enriched)
        return extraction

    async def _embed_chunks_in_batches(self, chunks: list[Chunk]) -> list[EmbeddedChunk]:
        batch_size = self._settings.embedding.batch_size
        embedded: list[EmbeddedChunk] = []
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            texts = [c.text for c in batch]
            outputs = await self._embedder.embed_documents(texts)
            for j, chunk in enumerate(batch):
                embedded.append(
                    EmbeddedChunk(
                        chunk=chunk,
                        dense_vector=outputs.dense_vectors[j],
                        sparse_indices=outputs.sparse_indices[j] if outputs.sparse_indices else [],
                        sparse_values=outputs.sparse_values[j] if outputs.sparse_values else [],
                    )
                )
        return embedded
