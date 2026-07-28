"""Unit tests for the enrichment (image-as-text) module."""

import types

import pytest

from gernas_rag.config.enrichment import EnrichmentConfig
from gernas_rag.enrichment.base import EnrichmentInput
from gernas_rag.enrichment.table_enricher import TableEnricher
from gernas_rag.enrichment.vision_llm_enricher import VisionLLMEnricher


class _StubEnricher:
    def __init__(self):
        self.calls = 0

    async def enrich(self, item):
        self.calls += 1
        from gernas_rag.enrichment.base import EnrichmentOutput

        return EnrichmentOutput(caption_text="delegated", model_name="stub", ok=True)


@pytest.mark.asyncio
async def test_table_enricher_skips_high_confidence():
    stub = _StubEnricher()
    enricher = TableEnricher(stub, confidence_threshold=0.7)
    out = await enricher.enrich(
        EnrichmentInput(image_bytes=b"x", mime_type="image/png", element_type="table", confidence=0.95)
    )
    assert out.ok is False
    assert stub.calls == 0  # No VLM call for a confidently-parsed table.


@pytest.mark.asyncio
async def test_table_enricher_delegates_low_confidence():
    stub = _StubEnricher()
    enricher = TableEnricher(stub, confidence_threshold=0.7)
    out = await enricher.enrich(
        EnrichmentInput(image_bytes=b"x", mime_type="image/png", element_type="table", confidence=0.4)
    )
    assert out.ok is True
    assert stub.calls == 1


@pytest.mark.asyncio
async def test_table_enricher_always_delegates_figures():
    stub = _StubEnricher()
    enricher = TableEnricher(stub, confidence_threshold=0.7)
    out = await enricher.enrich(
        EnrichmentInput(image_bytes=b"x", mime_type="image/png", element_type="figure")
    )
    assert out.ok is True
    assert stub.calls == 1


def _fake_anthropic_response(text: str):
    block = types.SimpleNamespace(type="text", text=text)
    return types.SimpleNamespace(content=[block])


@pytest.mark.asyncio
async def test_vision_enricher_success():
    cfg = EnrichmentConfig(enabled=True, provider="anthropic")
    enricher = VisionLLMEnricher(cfg, api_key="k")

    class _Msgs:
        async def create(self, **kwargs):
            return _fake_anthropic_response("Bar chart: NPL peaks at 4.2%")

    enricher._client = types.SimpleNamespace(messages=_Msgs())  # bypass _load()
    out = await enricher.enrich(
        EnrichmentInput(image_bytes=b"img", mime_type="image/png", element_type="figure")
    )
    assert out.ok is True
    assert "NPL peaks" in out.caption_text
    assert out.model_name == cfg.vlm_model_name


@pytest.mark.asyncio
async def test_vision_enricher_fail_soft():
    cfg = EnrichmentConfig(enabled=True, provider="anthropic")
    enricher = VisionLLMEnricher(cfg, api_key="k")

    class _Msgs:
        async def create(self, **kwargs):
            raise TimeoutError("VLM timed out")

    enricher._client = types.SimpleNamespace(messages=_Msgs())
    out = await enricher.enrich(
        EnrichmentInput(image_bytes=b"img", mime_type="image/png", element_type="figure")
    )
    assert out.ok is False
    assert out.caption_text == ""
    assert out.model_name is None
