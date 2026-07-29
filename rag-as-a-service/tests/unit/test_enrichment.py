"""Unit tests for the enrichment (image-as-text) module."""

import types

import pytest
from structlog.testing import capture_logs

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


def _fake_anthropic_response(text: str, stop_reason: str = "end_turn"):
    block = types.SimpleNamespace(type="text", text=text)
    return types.SimpleNamespace(content=[block], stop_reason=stop_reason)


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
async def test_vision_enricher_openai_compat_uses_base_url(monkeypatch):
    """provider='openai_compat' (e.g. Gemini's free-tier OpenAI-compatible API)
    must construct the OpenAI client with the configured base_url, and still
    route through the OpenAI chat-completions call path."""
    captured: dict = {}

    class _FakeAsyncOpenAI:
        def __init__(self, api_key=None, base_url=None):
            captured["api_key"] = api_key
            captured["base_url"] = base_url
            self.chat = types.SimpleNamespace(completions=self)

        async def create(self, **kwargs):
            captured["model"] = kwargs["model"]
            captured["reasoning_effort"] = kwargs.get("reasoning_effort")
            message = types.SimpleNamespace(content="Bar chart: NPL peaks at 4.2%")
            choice = types.SimpleNamespace(message=message, finish_reason="stop")
            return types.SimpleNamespace(choices=[choice])

    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeAsyncOpenAI)

    cfg = EnrichmentConfig(
        enabled=True,
        provider="openai_compat",
        vlm_model_name="gemini-flash-latest",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    )
    enricher = VisionLLMEnricher(cfg, api_key="gemini-key")
    out = await enricher.enrich(
        EnrichmentInput(image_bytes=b"img", mime_type="image/png", element_type="figure")
    )

    assert out.ok is True
    assert "NPL peaks" in out.caption_text
    assert captured["base_url"] == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert captured["api_key"] == "gemini-key"
    assert captured["model"] == "gemini-flash-latest"
    # Gemini "thinking" models burn part of max_tokens on hidden reasoning before
    # writing any visible output (see test_vision_enricher_anthropic_truncation_logged
    # docstring) — turned down for this pure-transcription task.
    assert captured["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_vision_enricher_plain_openai_skips_reasoning_effort(monkeypatch):
    """reasoning_effort is a Gemini-specific workaround gated on the Gemini base_url
    — real OpenAI (no base_url override) must not receive it, since OpenAI's own
    reasoning models reject "low" from a non-reasoning-model call path and plain
    chat models don't accept the field at all."""
    captured: dict = {}

    class _FakeAsyncOpenAI:
        def __init__(self, api_key=None, base_url=None):
            self.chat = types.SimpleNamespace(completions=self)

        async def create(self, **kwargs):
            captured.update(kwargs)
            message = types.SimpleNamespace(content="A chart")
            choice = types.SimpleNamespace(message=message, finish_reason="stop")
            return types.SimpleNamespace(choices=[choice])

    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeAsyncOpenAI)

    cfg = EnrichmentConfig(enabled=True, provider="openai", vlm_model_name="gpt-4o")
    enricher = VisionLLMEnricher(cfg, api_key="k")
    await enricher.enrich(
        EnrichmentInput(image_bytes=b"img", mime_type="image/png", element_type="figure")
    )
    assert "reasoning_effort" not in captured


@pytest.mark.asyncio
async def test_vision_enricher_anthropic_truncation_logged(caplog):
    """A response that hits max_tokens mid-transcription must still be kept
    (fail-soft favours a partial caption over none) but must log loudly —
    a silently clipped caption reads as complete and nobody would think to
    check it against the source image."""
    cfg = EnrichmentConfig(enabled=True, provider="anthropic", max_tokens=64)
    enricher = VisionLLMEnricher(cfg, api_key="k")

    class _Msgs:
        async def create(self, **kwargs):
            return _fake_anthropic_response("Chart title: Fees by band. X-Axis:", stop_reason="max_tokens")

    enricher._client = types.SimpleNamespace(messages=_Msgs())
    with capture_logs() as logs:
        out = await enricher.enrich(
            EnrichmentInput(image_bytes=b"img", mime_type="image/png", element_type="figure")
        )
    assert out.ok is True
    assert out.caption_text == "Chart title: Fees by band. X-Axis:"  # partial caption kept
    assert any("truncated" in log["event"].lower() for log in logs)


@pytest.mark.asyncio
async def test_vision_enricher_openai_truncation_logged():
    cfg = EnrichmentConfig(enabled=True, provider="openai")
    enricher = VisionLLMEnricher(cfg, api_key="k")

    class _Completions:
        async def create(self, **kwargs):
            message = types.SimpleNamespace(content="Row 1: 4.2%. Row 2:")
            choice = types.SimpleNamespace(message=message, finish_reason="length")
            return types.SimpleNamespace(choices=[choice])

    enricher._client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=_Completions()))
    with capture_logs() as logs:
        out = await enricher.enrich(
            EnrichmentInput(image_bytes=b"img", mime_type="image/png", element_type="table")
        )
    assert out.ok is True
    assert out.caption_text == "Row 1: 4.2%. Row 2:"
    assert any("truncated" in log["event"].lower() for log in logs)


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
