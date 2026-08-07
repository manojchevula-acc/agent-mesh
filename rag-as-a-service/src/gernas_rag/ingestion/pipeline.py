"""IngestionPipeline — orchestrates Extract → Chunk → Embed → Upsert."""

import asyncio
from pathlib import Path

from typing import TYPE_CHECKING

from ..chunking.factory import get_chunker
from ..config.settings import Settings
from ..embeddings.base import BaseEmbedder
from ..extraction.factory import get_extractor
from ..models.asset import ContentType
from ..models.chunk import Chunk, EmbeddedChunk
from ..models.ingestion import IngestionResult, IngestionStatus
from ..utils.logging import get_logger
from ..vectordb.base import BaseVectorDB
from .metadata import MetadataExtractor

if TYPE_CHECKING:
    from .image_pipeline import ImageIngestionPipeline

logger = get_logger(__name__)


class IngestionPipeline:
    """Orchestrates the full ingestion flow:

    ``Document file → Extract → Chunk → Embed → Upsert to VectorDB``

    Designed for async operation. Embedding batches run in a thread pool. Supports
    concurrent document processing via ``asyncio.gather`` with bounded concurrency.
    Idempotent — re-running on the same document updates existing chunks.
    """

    def __init__(
        self,
        settings: Settings,
        embedder: BaseEmbedder,
        vectordb: BaseVectorDB,
        image_pipeline: "ImageIngestionPipeline | None" = None,
    ) -> None:
        self._settings = settings
        self._embedder = embedder
        self._vectordb = vectordb
        self._image_pipeline = image_pipeline
        self._chunker = get_chunker(settings.chunking)
        self._metadata = MetadataExtractor()
        # Extractor is shared across all files — avoids reloading Docling weights per document.
        self._extractor = get_extractor(settings.chunking, Path("placeholder.pdf"))

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

            # Step 2: Chunk with metadata
            base_metadata = self._metadata.build_base_metadata(
                file_path,
                document_type,
                product_applicability,
                effective_date,
                raw_text=extraction.raw_markdown,
                original_name=original_name,
            )
            chunks = self._chunker.chunk(extraction, base_metadata)

            # Step 3: Embed in batches
            embedded_chunks = await self._embed_chunks_in_batches(chunks)

            # Step 4: Upsert, then drop any points this document left behind
            # under different chunk ids (boundaries change when table protection
            # is toggled, or when Docling degrades under memory pressure).
            count = await self._vectordb.upsert(embedded_chunks)
            try:
                await self._vectordb.reconcile_document(
                    base_metadata["document_name"], [c.id for c in chunks]
                )
            except Exception as exc:  # noqa: BLE001 - never fail an ingest on cleanup
                logger.warning(
                    "Stale-chunk reconcile failed",
                    file=str(file_path),
                    error=str(exc),
                )

            # Step 5: Image sub-pipeline (skipped entirely when disabled).
            images_indexed = figures_indexed = table_crops_indexed = 0
            if self._image_pipeline is not None:
                try:
                    image_result = await self._image_pipeline.ingest_images(
                        file_path, extraction, chunks, base_metadata
                    )
                    images_indexed = image_result.images_indexed
                    figures_indexed = image_result.figures
                    table_crops_indexed = image_result.table_crops
                except Exception as exc:  # noqa: BLE001
                    # Image indexing NEVER fails a text ingestion. Degrading to
                    # the text-only behaviour is always acceptable; losing the
                    # document is not.
                    logger.error(
                        "Image ingestion failed; text ingestion stands",
                        file=str(file_path),
                        error=str(exc),
                    )

            tables = sum(
                1 for c in chunks if c.metadata.content_type == ContentType.TABLE.value
            )
            logger.info(
                "Ingestion complete",
                file=str(file_path),
                chunks_upserted=count,
                tables=tables,
                images_indexed=images_indexed,
            )
            return IngestionResult(
                file_path=str(file_path),
                chunks_created=count,
                images_indexed=images_indexed,
                figures_indexed=figures_indexed,
                table_crops_indexed=table_crops_indexed,
                tables_found=tables,
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
