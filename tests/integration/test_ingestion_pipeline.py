"""Integration test: ingestion pipeline end-to-end with fakes."""

from pathlib import Path

from gernas_rag.config.settings import Settings
from gernas_rag.extraction.base import ExtractionResult
from gernas_rag.ingestion.pipeline import IngestionPipeline


async def test_ingest_file_end_to_end(monkeypatch, fake_embedder, fake_vectordb, sample_extraction):
    settings = Settings(_env_file=None, redis_enabled=False)  # type: ignore[call-arg]
    pipeline = IngestionPipeline(settings, fake_embedder, fake_vectordb)

    # Patch extractor selection to avoid needing Docling / a real file.
    class _FakeExtractor:
        async def extract(self, file_path: Path) -> ExtractionResult:
            return sample_extraction

        def supports(self, file_path: Path) -> bool:
            return True

    monkeypatch.setattr(
        "gernas_rag.ingestion.pipeline.get_extractor",
        lambda config, file_path: _FakeExtractor(),
    )

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
    pipeline = IngestionPipeline(settings, fake_embedder, fake_vectordb)

    class _BrokenExtractor:
        async def extract(self, file_path: Path):
            raise RuntimeError("boom")

        def supports(self, file_path: Path) -> bool:
            return True

    monkeypatch.setattr(
        "gernas_rag.ingestion.pipeline.get_extractor",
        lambda config, file_path: _BrokenExtractor(),
    )

    result = await pipeline.ingest_file(Path("broken.pdf"), document_type="other")
    # Pipeline must not raise — it reports the error instead.
    assert result.status == "error"
    assert result.chunks_created == 0
