"""Query routing — decides whether the image branch runs at all."""

import pytest

from gernas_rag.config.multimodal import ImageIntent, MultimodalRetrievalConfig
from gernas_rag.models.retrieval import ImageQuery, RetrieveRequest
from gernas_rag.retrieval.intent import ImageIntentRouter


def _router(**overrides) -> ImageIntentRouter:
    return ImageIntentRouter(MultimodalRetrievalConfig(**overrides))


def _request(query: str = "what is the pricing floor", **kwargs) -> RetrieveRequest:
    return RetrieveRequest(query=query, **kwargs)


# ── Explicit overrides beat everything ───────────────────────────────────
def test_explicit_true_overrides_never():
    router = _router(image_intent=ImageIntent.NEVER)
    assert router.wants_images(_request(include_images=True)) is True


def test_explicit_false_overrides_always():
    router = _router(image_intent=ImageIntent.ALWAYS)
    assert router.wants_images(_request(include_images=False)) is False


def test_image_query_forces_image_search():
    router = _router(image_intent=ImageIntent.NEVER)
    request = _request(query_image=ImageQuery(asset_id="a" * 32))
    assert router.wants_images(request) is True


def test_modalities_list_is_honoured():
    router = _router(image_intent=ImageIntent.NEVER)
    assert router.wants_images(_request(modalities=["text", "image"])) is True
    assert router.wants_images(_request(modalities=["text"])) is False


# ── Modes ────────────────────────────────────────────────────────────────
def test_always_and_never():
    assert _router(image_intent=ImageIntent.ALWAYS).wants_images(_request()) is True
    assert _router(image_intent=ImageIntent.NEVER).wants_images(_request()) is False


# ── Heuristic ────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "query",
    [
        "show me the credit approval authority matrix diagram",
        "what does the tiering CHART look like",
        "is there a flowchart for the approval process",
        "find the org chart for risk",
        "which figure covers concentration limits",
    ],
)
def test_heuristic_hits(query):
    assert _router().wants_images(_request(query)) is True


@pytest.mark.parametrize(
    "query",
    [
        "what is the minimum pricing floor for a BB-rated corporate term loan",
        "who approves an AED 50M facility",
        "when did CBUAE circular 2024/047 take effect",
    ],
)
def test_heuristic_misses_on_pure_text_questions(query):
    assert _router().wants_images(_request(query)) is False


def test_heuristic_is_case_insensitive():
    assert _router().wants_images(_request("Show Me The DIAGRAM please")) is True


def test_keywords_are_configurable():
    router = _router(intent_keywords=["annexure b"])
    assert router.wants_images(_request("please pull annexure b for me")) is True
    assert router.wants_images(_request("show me the diagram")) is False
