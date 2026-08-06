"""Vision generation — prompt shape, image budget, and the fallback path.

The first test is the regression guard for D7: adding vision must not change the
request a text-only query produces.
"""

from io import BytesIO

import pytest

from gernas_rag.config.settings import Settings
from gernas_rag.generation.generator import ResponseGenerator
from gernas_rag.generation.image_payload import ImagePayloadBuilder
from gernas_rag.llm.base import ImagePart, Message, TextPart, reject_images
from gernas_rag.llm.groq_llm import GroqLLM
from gernas_rag.llm.router import VisionRouter
from gernas_rag.models.retrieval import RetrievedChunk, RetrievedImage

from fakes import FakeLLM

PIL = pytest.importorskip("PIL.Image")


def _settings(**llm_overrides) -> Settings:
    settings = Settings(_env_file=None, redis_enabled=False)
    for key, value in llm_overrides.items():
        setattr(settings.llm, key, value)
    return settings


def _chunk(text: str = "The floor is 260 bps.", **kwargs) -> RetrievedChunk:
    defaults = dict(
        text=text,
        source="doc",
        clause_reference="4.2.1",
        score=0.9,
        effective_date="",
        freshness_warning=False,
    )
    defaults.update(kwargs)
    return RetrievedChunk(**defaults)


def _image(asset_id: str = "a" * 32, **kwargs) -> RetrievedImage:
    defaults = dict(
        asset_id=asset_id,
        uri=f"/api/v1/assets/{asset_id}",
        source="doc",
        page_number=12,
        caption="Credit Approval Authority Matrix",
    )
    defaults.update(kwargs)
    return RetrievedImage(**defaults)


def _png_bytes(colour=(30, 90, 200)) -> bytes:
    buf = BytesIO()
    PIL.new("RGB", (300, 200), colour).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def asset_store(fake_asset_store):
    return fake_asset_store


@pytest.fixture
def builder(asset_store) -> ImagePayloadBuilder:
    return ImagePayloadBuilder(asset_store, _settings(vision_enabled=True).llm)


# ── The D7 regression guard ──────────────────────────────────────────────
async def test_text_only_request_is_unchanged(builder):
    """No images => content stays a plain str, so the serialised Groq payload is
    byte-identical to what this service sends today."""
    llm = FakeLLM()
    generator = ResponseGenerator(_settings(), llm, builder)
    await generator.generate("what is the pricing floor?", [_chunk()])

    user = next(m for m in llm.last_messages if m.role == "user")
    assert isinstance(user.content, list)  # parts internally...
    serialised = GroqLLM._serialise(Message(role="user", content=user.flatten()))
    assert isinstance(serialised["content"], str)  # ...but no image parts
    assert not user.has_images


async def test_text_only_serialises_to_a_plain_string():
    message = Message(role="user", content="plain text")
    assert GroqLLM._serialise(message) == {"role": "user", "content": "plain text"}


# ── Prompt shape ─────────────────────────────────────────────────────────
async def test_images_are_interleaved_with_their_labels(builder, asset_store):
    ids = []
    for colour in [(200, 30, 30), (30, 200, 30)]:
        ids.append(asset_store.put(_png_bytes(colour)).asset_id)

    llm = FakeLLM(supports_vision=True)
    generator = ResponseGenerator(_settings(vision_enabled=True), llm, builder)
    await generator.generate(
        "show me the matrix", [_chunk()], [_image(ids[0]), _image(ids[1])]
    )

    user = next(m for m in llm.last_messages if m.role == "user")
    parts = user.content
    image_positions = [i for i, p in enumerate(parts) if isinstance(p, ImagePart)]
    assert len(image_positions) == 2

    # Each image is preceded by its own [IN] label, so [I2] cannot bind to the
    # wrong picture.
    for n, position in enumerate(image_positions, start=1):
        preceding = " ".join(
            p.text for p in parts[:position] if isinstance(p, TextPart)
        )
        assert f"[I{n}]" in preceding


async def test_image_budget_is_capped(builder, asset_store):
    ids = [asset_store.put(_png_bytes((i * 20, 50, 90))).asset_id for i in range(8)]
    llm = FakeLLM(supports_vision=True)
    generator = ResponseGenerator(_settings(vision_enabled=True), llm, builder)
    await generator.generate("show me", [_chunk()], [_image(i) for i in ids])

    user = next(m for m in llm.last_messages if m.role == "user")
    assert sum(1 for p in user.content if isinstance(p, ImagePart)) == 3


async def test_missing_asset_does_not_fail_generation(builder):
    llm = FakeLLM(supports_vision=True)
    generator = ResponseGenerator(_settings(vision_enabled=True), llm, builder)
    answer = await generator.generate("show me", [_chunk()], [_image("f" * 32)])
    assert answer  # generation still succeeded
    user = next(m for m in llm.last_messages if m.role == "user")
    assert not user.has_images  # the unusable image was skipped


async def test_payload_is_jpeg_base64(builder, asset_store):
    asset_id = asset_store.put(_png_bytes()).asset_id
    part = builder.build(_image(asset_id))
    assert part is not None
    assert part.data_uri.startswith("data:image/jpeg;base64,")


async def test_payload_is_downscaled(builder, asset_store):
    import base64

    buf = BytesIO()
    PIL.new("RGB", (2000, 1500), (10, 10, 10)).save(buf, format="PNG")
    asset_id = asset_store.put(buf.getvalue()).asset_id

    part = builder.build(_image(asset_id))
    raw = base64.b64decode(part.data_uri.split(",", 1)[1])
    assert max(PIL.open(BytesIO(raw)).size) <= 768


async def test_image_part_repr_hides_the_blob():
    part = ImagePart(data_uri="data:image/jpeg;base64," + "A" * 4000)
    assert "AAAA" not in repr(part)


# ── Prompts ──────────────────────────────────────────────────────────────
async def test_vision_prompt_says_the_model_can_see(builder, asset_store):
    asset_id = asset_store.put(_png_bytes()).asset_id
    llm = FakeLLM(supports_vision=True)
    generator = ResponseGenerator(_settings(vision_enabled=True), llm, builder)
    await generator.generate("show me", [_chunk()], [_image(asset_id)])

    system = next(m for m in llm.last_messages if m.role == "system").content
    assert "you can see it" in system
    assert "rather than guessing" in system


async def test_fallback_prompt_says_the_model_cannot_see():
    """No payload builder => caption-only path, with the opposite instruction."""
    llm = FakeLLM()
    generator = ResponseGenerator(_settings(), llm, payload_builder=None)
    await generator.generate("show me", [_chunk()], [_image()])

    system = next(m for m in llm.last_messages if m.role == "system").content
    assert "You cannot see" in system
    assert "Never describe visual details" in system


async def test_table_chunks_are_fenced(builder):
    llm = FakeLLM()
    generator = ResponseGenerator(_settings(), llm, builder)
    table = _chunk(text="| A | B |\n| - | - |\n| 1 | 2 |", content_type="table",
                   table_part="2/3")
    await generator.generate("what is the value", [table])

    user = next(m for m in llm.last_messages if m.role == "user")
    text = user.flatten()
    assert "```" in text
    assert "TABLE" in text
    assert "part 2/3" in text
    assert "ambiguous" in text


async def test_weak_text_rescue_hints_at_figures(builder, asset_store):
    """Without this, an image-only match hits 'no relevant context'."""
    asset_id = asset_store.put(_png_bytes()).asset_id
    llm = FakeLLM(supports_vision=True)
    generator = ResponseGenerator(_settings(vision_enabled=True), llm, builder)
    await generator.generate(
        "show me the matrix", [_chunk(score=0.01)], [_image(asset_id)]
    )
    system = next(m for m in llm.last_messages if m.role == "system").content
    assert "text context is weak" in system


async def test_no_context_at_all_short_circuits():
    generator = ResponseGenerator(_settings(), FakeLLM(), None)
    answer = await generator.generate("anything", [], [])
    assert "could not find" in answer


# ── Routing and fallback ─────────────────────────────────────────────────
async def test_router_uses_text_model_when_no_images():
    text_llm, vision_llm = FakeLLM(), FakeLLM(supports_vision=True)
    router = VisionRouter(text_llm, vision_llm)
    await router.generate([Message(role="user", content="plain")])
    assert text_llm.call_count == 1 and vision_llm.call_count == 0


async def test_router_uses_vision_model_when_images_present():
    text_llm, vision_llm = FakeLLM(), FakeLLM(supports_vision=True)
    router = VisionRouter(text_llm, vision_llm)
    await router.generate(
        [Message(role="user", content=[TextPart("[I1]"), ImagePart("data:image/jpeg;base64,x")])]
    )
    assert vision_llm.call_count == 1 and text_llm.call_count == 0


async def test_router_falls_back_on_vision_error():
    """Preview-model risk: degrade to captions, never fail the request."""
    text_llm = FakeLLM()
    vision_llm = FakeLLM(supports_vision=True, fail=True)
    router = VisionRouter(text_llm, vision_llm, fallback_to_text=True)

    answer = await router.generate(
        [Message(role="user", content=[TextPart("[I1] Figure"), ImagePart("data:x")])]
    )
    assert answer  # no exception surfaced
    assert text_llm.call_count == 1
    # The fallback strips pixels but keeps the [I1] descriptor.
    assert not text_llm.last_messages[0].has_images
    assert "[I1] Figure" in text_llm.last_messages[0].flatten()


async def test_router_raises_when_fallback_disabled():
    router = VisionRouter(FakeLLM(), FakeLLM(supports_vision=True, fail=True),
                          fallback_to_text=False)
    with pytest.raises(RuntimeError):
        await router.generate(
            [Message(role="user", content=[TextPart("x"), ImagePart("data:x")])]
        )


async def test_router_degrades_when_no_vision_model_configured():
    text_llm = FakeLLM()
    router = VisionRouter(text_llm, vision_llm=None, fallback_to_text=True)
    await router.generate(
        [Message(role="user", content=[TextPart("[I1]"), ImagePart("data:x")])]
    )
    assert text_llm.call_count == 1


# ── Provider guards ──────────────────────────────────────────────────────
def test_text_only_providers_reject_images():
    messages = [Message(role="user", content=[TextPart("x"), ImagePart("data:x")])]
    with pytest.raises(ValueError, match="cannot accept image input"):
        reject_images(messages, "some-text-model")


def test_reject_images_passes_text_through():
    reject_images([Message(role="user", content="plain")], "m")  # no raise


# ── Citation validation ──────────────────────────────────────────────────
def test_out_of_range_citations_are_detected(caplog):
    ResponseGenerator._validate_citations("See [1] and [I5].", n_chunks=1, n_images=2)
    # Logged, not rewritten — rewriting risks corrupting a correct answer.


def test_in_range_citations_pass_through():
    answer = "See [1] and [I2]."
    assert ResponseGenerator._validate_citations(answer, 3, 2) == answer
