"""Unit tests for the generator's config-gated hydration branch."""

import pytest

from gernas_rag.config.settings import Settings
from gernas_rag.generation.generator import ResponseGenerator
from gernas_rag.models.retrieval import RetrievedChunk


class _RecordingLLM:
    def __init__(self, name):
        self.name = name
        self.last_messages = None

    async def generate(self, messages):
        self.last_messages = messages
        return f"answer-from-{self.name}"

    async def health_check(self):
        return True


class _FakeArtifactStore:
    def __init__(self):
        self.requested_refs = []

    async def put_bytes(self, data, mime_type):
        return "sha256:abc.png"

    async def get_bytes(self, ref):
        self.requested_refs.append(ref)
        return b"IMAGEBYTES", "image/png"


def _chunk(modality="text", ref=None):
    return RetrievedChunk(
        text="Some transcribed content",
        source="FAB Policy",
        clause_reference="4.2.1",
        score=0.9,
        effective_date="",
        freshness_warning=False,
        modality=modality,
        artifact_ref=ref,
    )


def _settings(hydration_enabled: bool, mode: str = "conditional") -> Settings:
    s = Settings()
    return s.model_copy(
        update={
            "hydration": s.hydration.model_copy(
                update={"enabled": hydration_enabled, "mode": mode}
            )
        }
    )


@pytest.mark.asyncio
async def test_disabled_uses_text_llm_no_store_read():
    text_llm = _RecordingLLM("text")
    vision_llm = _RecordingLLM("vision")
    store = _FakeArtifactStore()
    gen = ResponseGenerator(_settings(False), text_llm, artifact_store=store, vision_llm=vision_llm)

    ans = await gen.generate("q", [_chunk("figure", "sha256:abc.png")])
    assert ans == "answer-from-text"
    assert store.requested_refs == []  # No hydration read when disabled.


@pytest.mark.asyncio
async def test_conditional_hydrates_figure_and_routes_to_vision():
    text_llm = _RecordingLLM("text")
    vision_llm = _RecordingLLM("vision")
    store = _FakeArtifactStore()
    gen = ResponseGenerator(_settings(True), text_llm, artifact_store=store, vision_llm=vision_llm)

    ans = await gen.generate("q", [_chunk("figure", "sha256:abc.png")])
    assert ans == "answer-from-vision"
    # The exact ref stored on the chunk is what gets loaded (linkage guarantee).
    assert store.requested_refs == ["sha256:abc.png"]
    # The user message carries an image part.
    user_msg = [m for m in vision_llm.last_messages if m.role == "user"][0]
    assert any(getattr(p, "type", "") == "image" for p in user_msg.content)


@pytest.mark.asyncio
async def test_conditional_skips_text_modality():
    text_llm = _RecordingLLM("text")
    vision_llm = _RecordingLLM("vision")
    store = _FakeArtifactStore()
    gen = ResponseGenerator(_settings(True), text_llm, artifact_store=store, vision_llm=vision_llm)

    ans = await gen.generate("q", [_chunk("text", None)])
    assert ans == "answer-from-text"
    assert store.requested_refs == []


@pytest.mark.asyncio
async def test_hydration_fail_soft_to_text():
    text_llm = _RecordingLLM("text")
    vision_llm = _RecordingLLM("vision")

    class _BrokenStore(_FakeArtifactStore):
        async def get_bytes(self, ref):
            raise FileNotFoundError(ref)

    gen = ResponseGenerator(_settings(True), text_llm, artifact_store=_BrokenStore(), vision_llm=vision_llm)
    ans = await gen.generate("q", [_chunk("figure", "sha256:missing.png")])
    # Missing artifact → no image parts → falls back to the text LLM, no crash.
    assert ans == "answer-from-text"


@pytest.mark.asyncio
async def test_context_marks_modality():
    gen = ResponseGenerator(_settings(False), _RecordingLLM("text"))
    ctx = gen._build_context([_chunk("figure", "sha256:abc.png")])
    assert "[Figure]" in ctx
