"""Integration test: ingestion pipeline end-to-end with fakes."""

from pathlib import Path

from gernas_rag.config.settings import Settings
from gernas_rag.extraction.base import ExtractionResult
from gernas_rag.ingestion.pipeline import IngestionPipeline


async def test_ingest_file_end_to_end(monkeypatch, fake_embedder, fake_vectordb, sample_extraction):
    settings = Settings(_env_file=None, redis_enabled=False)  # type: ignore[call-arg]

    # Patch extractor selection to avoid needing Docling / a real file. This must
    # happen BEFORE constructing the pipeline: __init__ resolves the extractor
    # once and caches it, so patching afterwards has no effect.
    class _FakeExtractor:
        async def extract(self, file_path: Path) -> ExtractionResult:
            return sample_extraction

        def supports(self, file_path: Path) -> bool:
            return True

    monkeypatch.setattr(
        "gernas_rag.ingestion.pipeline.get_extractor",
        lambda config, file_path: _FakeExtractor(),
    )
    pipeline = IngestionPipeline(settings, fake_embedder, fake_vectordb)

    result = await pipeline.ingest_file(
        Path("FAB_Credit_Pricing_Policy_v2.4.pdf"),
        document_type="pricing_policy",
        product_applicability=["corporate_loan"],
        effective_date="2024-06-01",
    )

    assert result.status == "success"
    assert result.chunks_created > 0
    assert len(fake_vectordb.store) == result.chunks_created


async def test_ingest_file_handles_extraction_error(monkeypatch, fake_embedder, fake_vectordb):
    settings = Settings(_env_file=None, redis_enabled=False)  # type: ignore[call-arg]

    class _BrokenExtractor:
        async def extract(self, file_path: Path):
            raise RuntimeError("boom")

        def supports(self, file_path: Path) -> bool:
            return True

    monkeypatch.setattr(
        "gernas_rag.ingestion.pipeline.get_extractor",
        lambda config, file_path: _BrokenExtractor(),
    )
    pipeline = IngestionPipeline(settings, fake_embedder, fake_vectordb)

    result = await pipeline.ingest_file(Path("broken.pdf"), document_type="other")
    # Pipeline must not raise — it reports the error instead.
    assert result.status == "error"
    assert result.chunks_created == 0


async def test_image_pipeline_failure_never_fails_text_ingestion(
    monkeypatch, fake_embedder, fake_vectordb, sample_extraction
):
    """The image sub-pipeline is an enhancement, not a hard dependency."""
    settings = Settings(_env_file=None, redis_enabled=False)  # type: ignore[call-arg]

    class _FakeExtractor:
        async def extract(self, file_path: Path) -> ExtractionResult:
            return sample_extraction

        def supports(self, file_path: Path) -> bool:
            return True

    class _BrokenImagePipeline:
        async def ingest_images(self, *args, **kwargs):
            raise RuntimeError("pymupdf exploded")

    monkeypatch.setattr(
        "gernas_rag.ingestion.pipeline.get_extractor",
        lambda config, file_path: _FakeExtractor(),
    )
    pipeline = IngestionPipeline(
        settings, fake_embedder, fake_vectordb, _BrokenImagePipeline()
    )

    result = await pipeline.ingest_file(Path("doc.pdf"), document_type="pricing_policy")
    assert result.status == "success"  # text ingestion stands
    assert result.chunks_created > 0
    assert result.images_indexed == 0
