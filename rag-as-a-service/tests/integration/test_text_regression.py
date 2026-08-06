"""The most important test in the plan: the text path must be untouched.

With multimodal off, MultimodalRetrievalPipeline is a pure pass-through. With
side_car on, images are ADDITIVE — they never reorder or displace text chunks.
"""

import pytest

from gernas_rag.config.multimodal import FusionMode, ImageIntent
from gernas_rag.config.settings import Settings
from gernas_rag.models.asset import EmbeddedImage
from gernas_rag.models.retrieval import RetrieveRequest
from gernas_rag.retrieval.multimodal_pipeline import MultimodalRetrievalPipeline
from gernas_rag.retrieval.pipeline import RetrievalPipeline

_IGNORED = {"latency_ms"}
_MULTIMODAL_FIELDS = {"images", "image_search_performed", "multimodal_space_id"}


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, api_key=None, redis_enabled=False)


async def _seed(store, embedder, concept: str, asset_id: str, factory) -> None:
    output = await embedder.embed_images([concept.encode()])
    await store.upsert_images(
        [
            EmbeddedImage(
                asset=factory(asset_id, concept),
                dense_vector=output.dense_vectors[0],
                space_id=embedder.space.space_id,
            )
        ]
    )


async def _populate(vectordb, embedder) -> None:
    from gernas_rag.models.chunk import Chunk, ChunkMetadata, EmbeddedChunk

    texts = [
        "The minimum pricing floor for a BB-rated facility is 260 basis points.",
        "A BBB-rated AED 25-100M facility requires Segment Credit Head approval.",
        "Concentration limits are reviewed quarterly by the risk committee.",
    ]
    output = await embedder.embed_documents(texts)
    await vectordb.upsert(
        [
            EmbeddedChunk(
                chunk=Chunk(
                    id=f"c{i}",
                    text=t,
                    metadata=ChunkMetadata(
                        document_name="doc", document_type="pricing_policy"
                    ),
                ),
                dense_vector=output.dense_vectors[i],
                sparse_indices=output.sparse_indices[i] if output.sparse_indices else [],
                sparse_values=output.sparse_values[i] if output.sparse_values else [],
            )
            for i, t in enumerate(texts)
        ]
    )


# ── Flag off: pure pass-through ──────────────────────────────────────────
async def test_flag_off_is_identical(settings, fake_embedder, fake_vectordb):
    await _populate(fake_vectordb, fake_embedder)
    settings.multimodal.enabled = False

    text_only = RetrievalPipeline(settings, fake_embedder, fake_vectordb)
    wrapped = MultimodalRetrievalPipeline(settings, text_only, None, None)

    request = RetrieveRequest(query="pricing floor for BB-rated corporate loans")
    direct = await text_only.retrieve(request)
    through_wrapper = await wrapped.retrieve(request)

    assert direct.model_dump(exclude=_IGNORED) == through_wrapper.model_dump(
        exclude=_IGNORED
    )
    assert through_wrapper.images == []
    assert through_wrapper.image_search_performed is False


async def test_flag_off_ignores_include_images(settings, fake_embedder, fake_vectordb):
    """A client asking for images on a text-only deployment gets text, not a 500."""
    await _populate(fake_vectordb, fake_embedder)
    settings.multimodal.enabled = False

    text_only = RetrievalPipeline(settings, fake_embedder, fake_vectordb)
    wrapped = MultimodalRetrievalPipeline(settings, text_only, None, None)
    response = await wrapped.retrieve(
        RetrieveRequest(query="show me the diagram", include_images=True)
    )
    assert response.images == []
    assert response.chunks


# ── Side-car: images are additive ────────────────────────────────────────
async def test_side_car_does_not_reorder_text(
    settings,
    fake_embedder,
    fake_vectordb,
    fake_multimodal_embedder,
    fake_image_store,
    image_asset_factory,
):
    await _populate(fake_vectordb, fake_embedder)
    await _seed(
        fake_image_store, fake_multimodal_embedder, "org chart", "a" * 32,
        image_asset_factory,
    )

    settings.multimodal.enabled = True
    settings.multimodal.retrieval.mode = FusionMode.SIDE_CAR
    settings.multimodal.retrieval.image_intent = ImageIntent.ALWAYS
    settings.multimodal.retrieval.image_score_floor = 0.0

    text_only = RetrievalPipeline(settings, fake_embedder, fake_vectordb)
    baseline = await text_only.retrieve(RetrieveRequest(query="org chart for risk"))

    wrapped = MultimodalRetrievalPipeline(
        settings, text_only, fake_multimodal_embedder, fake_image_store, score_floor=0.0
    )
    response = await wrapped.retrieve(RetrieveRequest(query="org chart for risk"))

    assert [c.text for c in response.chunks] == [c.text for c in baseline.chunks]
    assert response.image_search_performed is True
    assert response.multimodal_space_id == "fake0000"


async def test_mode_off_skips_the_image_branch(
    settings, fake_embedder, fake_vectordb, fake_multimodal_embedder, fake_image_store
):
    await _populate(fake_vectordb, fake_embedder)
    settings.multimodal.enabled = True
    settings.multimodal.retrieval.mode = FusionMode.OFF

    text_only = RetrievalPipeline(settings, fake_embedder, fake_vectordb)
    wrapped = MultimodalRetrievalPipeline(
        settings, text_only, fake_multimodal_embedder, fake_image_store
    )
    response = await wrapped.retrieve(RetrieveRequest(query="show me the diagram"))
    assert response.image_search_performed is False


async def test_intent_router_skips_pure_text_queries(
    settings, fake_embedder, fake_vectordb, fake_multimodal_embedder, fake_image_store
):
    await _populate(fake_vectordb, fake_embedder)
    settings.multimodal.enabled = True
    settings.multimodal.retrieval.image_intent = ImageIntent.HEURISTIC

    text_only = RetrievalPipeline(settings, fake_embedder, fake_vectordb)
    wrapped = MultimodalRetrievalPipeline(
        settings, text_only, fake_multimodal_embedder, fake_image_store
    )
    response = await wrapped.retrieve(
        RetrieveRequest(query="what is the minimum pricing floor for BB-rated loans")
    )
    assert response.image_search_performed is False


# ── Resilience ───────────────────────────────────────────────────────────
async def test_image_branch_failure_degrades_to_text(
    settings, fake_embedder, fake_vectordb, fake_multimodal_embedder, fake_image_store
):
    await _populate(fake_vectordb, fake_embedder)
    settings.multimodal.enabled = True
    settings.multimodal.retrieval.image_intent = ImageIntent.ALWAYS

    async def _boom(*args, **kwargs):
        raise RuntimeError("qdrant unreachable")

    fake_image_store.dense_search = _boom

    text_only = RetrievalPipeline(settings, fake_embedder, fake_vectordb)
    wrapped = MultimodalRetrievalPipeline(
        settings, text_only, fake_multimodal_embedder, fake_image_store
    )
    response = await wrapped.retrieve(RetrieveRequest(query="show me the diagram"))
    assert response.chunks  # text still served
    assert response.images == []


# ── Cache namespacing (R6) ───────────────────────────────────────────────
def test_cache_key_depends_on_configuration():
    """Flipping the flag must invalidate cached responses, even though the
    request payload is byte-identical."""
    from gernas_rag.cache.redis_cache import RAGCache

    request = RetrieveRequest(query="show me the approval matrix")
    off = RAGCache("redis://x", 900, enabled=False, key_namespace="0:side_car:-:0")
    on = RAGCache("redis://x", 900, enabled=False, key_namespace="1:side_car:abc123:1")
    assert off.make_key(request) != on.make_key(request)


def test_cache_key_is_stable_for_the_same_config():
    from gernas_rag.cache.redis_cache import RAGCache

    request = RetrieveRequest(query="same query")
    cache = RAGCache("redis://x", 900, enabled=False, key_namespace="ns")
    assert cache.make_key(request) == cache.make_key(request)
