"""Behavioural tests for the MAF retrieval workflow.

These replace the migration-era parity suite. That suite proved the workflow
matched the pre-migration inline implementation by running both and diffing; the
inline path no longer exists, so these assert the workflow's contract directly:
ordering, freshness flags, parent expansion, multimodal metadata pass-through,
empty results, reranker fallback, and — most importantly — that an executor's
exception propagates out of ``workflow.run()`` unchanged.
"""

from datetime import datetime, timedelta, timezone

import pytest

from gernas_rag.config.embedding import EmbeddingProvider
from gernas_rag.models.chunk import Chunk, ChunkMetadata, EmbeddedChunk, Modality
from gernas_rag.models.retrieval import DocumentFilter, RetrieveRequest
from gernas_rag.retrieval.pipeline import _FRESHNESS_WARNING_THRESHOLD, RetrievalPipeline
from gernas_rag.vectordb.base import SearchResult


_NOW = datetime.now(timezone.utc)
# Comfortably inside RetrievalConfig.freshness_max_age_days (180).
_FRESH_DATE = (_NOW - timedelta(days=30)).strftime("%Y-%m-%d")
# Past 2x max_age, so freshness decays to 0 and the warning always trips.
_STALE_DATE = (_NOW - timedelta(days=1500)).strftime("%Y-%m-%d")


class FakeReranker:
    """Deterministic stand-in for the cross-encoder — same ``rerank`` surface.

    The real Reranker loads ~2GB of bge-reranker-v2-m3 weights through
    FlagEmbedding/transformers/torch, which is slow and, on Windows, prone to a
    native access violation during load. A unit test has no business doing that:
    what needs testing here is that RerankExecutor calls the reranker and honours
    top_n, not that FlagEmbedding works.

    Reranks by text length descending, so the ordering is observably different
    from the fused input and a no-op executor would be caught.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def rerank(self, query: str, results: list[SearchResult], top_n: int) -> list[SearchResult]:
        self.calls.append((query, top_n))
        ranked = sorted(results, key=lambda r: len(r.text), reverse=True)[:top_n]
        return [
            SearchResult(r.chunk_id, r.text, r.score, r.metadata, i) for i, r in enumerate(ranked)
        ]


@pytest.fixture
def fake_reranker(monkeypatch) -> FakeReranker:
    """Make every RetrievalPipeline built in this module use the fake.

    Patched at ``Reranker.from_embedding_config`` because RerankExecutor captures
    the reranker instance at workflow-build time — swapping ``pipeline._reranker``
    afterwards would not reach the executor.
    """
    stub = FakeReranker()
    monkeypatch.setattr(
        "gernas_rag.retrieval.pipeline.Reranker.from_embedding_config",
        classmethod(lambda cls, cfg: stub),
    )
    return stub


async def _seed(vectordb, count: int = 12) -> None:
    """Populate the fake vector DB with a deterministic corpus.

    Deliberately mixes modalities, effective dates and parent links so the tests
    exercise freshness penalties, media metadata pass-through and parent
    expansion rather than just the happy path.
    """
    chunks: list[EmbeddedChunk] = []

    parent_meta = ChunkMetadata(document_name="pricing_policy.pdf", document_type="pricing_policy")
    parent = Chunk(
        id="parent-1",
        text="Section 4.2 Pricing floors. " + ("Full parent section body. " * 20),
        metadata=parent_meta,
        is_parent=True,
    )
    chunks.append(EmbeddedChunk(chunk=parent, dense_vector=[0.1] * 8))

    for i in range(count):
        # ChunkMetadata is frozen — every field must be supplied at construction.
        meta = ChunkMetadata(
            document_name=f"doc_{i % 3}.pdf",
            document_type="pricing_policy",
            section_heading=f"Section {i}",
            clause_reference=f"4.2.{i}",
            # Mix fresh and stale so the freshness penalty actually fires.
            # Dates are relative to today, not hardcoded: freshness_max_age_days
            # defaults to 180, so a fixed "recent" date silently becomes stale as
            # the calendar moves and the test would rot.
            effective_date=_FRESH_DATE if i % 2 == 0 else _STALE_DATE,
            modality=Modality.TABLE if i % 4 == 0 else Modality.TEXT,
            artifact_ref=f"sha256:artifact{i}" if i % 4 == 0 else None,
            parent_chunk_id="parent-1" if i % 3 == 0 else None,
        )
        chunk = Chunk(
            id=f"chunk-{i}", text=f"Clause {i}: pricing floor is {200 + i} bps.", metadata=meta
        )
        chunks.append(EmbeddedChunk(chunk=chunk, dense_vector=[0.1 * i] * 8))

    await vectordb.upsert(chunks)


@pytest.fixture
async def pipeline(settings, fake_embedder, fake_vectordb, fake_reranker):
    await _seed(fake_vectordb)
    return RetrievalPipeline(settings, fake_embedder, fake_vectordb)


async def test_workflow_is_built_and_reused(pipeline):
    """MAF workflows are immutable and built once. Rebuilding per request would be
    a silent performance regression."""
    first = pipeline._workflow
    assert first is not None

    await pipeline.retrieve(RetrieveRequest(query="first call"))
    await pipeline.retrieve(RetrieveRequest(query="second call"))

    assert pipeline._workflow is first


@pytest.mark.parametrize(
    "request_kwargs",
    [
        {"query": "what is the credit pricing floor"},
        {"query": "capital adequacy requirements", "top_k": 1},
        {"query": "concentration limits by sector", "top_k": 20},
        {"query": "term loan pricing grid", "include_parent": False},
        {"query": "fee schedule for trade finance", "include_parent": True},
        {"query": "pricing tables only", "filters": DocumentFilter(modality=["table"])},
        {
            "query": "policies effective after 2024",
            "filters": DocumentFilter(effective_date_from="2024-01-01"),
        },
    ],
)
async def test_returns_a_wellformed_response(pipeline, request_kwargs):
    request = RetrieveRequest(**request_kwargs)
    response = await pipeline.retrieve(request)

    assert response.total_results == len(response.chunks)
    assert len(response.chunks) <= request.top_k
    assert response.latency_ms >= 0
    assert response.freshness_warning_global == any(c.freshness_warning for c in response.chunks)


async def test_results_are_deterministic(pipeline):
    """Identical inputs must give identical rankings — the workflow must not leak
    state between runs via the shared executors."""
    request = RetrieveRequest(query="credit pricing floor by rating", top_k=10)

    first = [(c.source, c.clause_reference, c.score) for c in (await pipeline.retrieve(request)).chunks]
    second = [(c.source, c.clause_reference, c.score) for c in (await pipeline.retrieve(request)).chunks]

    assert first == second
    assert first, "corpus produced no results — the test would be vacuous"


async def test_chunks_ordered_by_descending_score(pipeline):
    """FreshnessExecutor re-sorts after applying the penalty; the response must
    come back best-first."""
    response = await pipeline.retrieve(RetrieveRequest(query="pricing floor", top_k=10))

    scores = [c.score for c in response.chunks]
    assert scores == sorted(scores, reverse=True)


async def test_freshness_warning_matches_threshold(pipeline):
    """freshness_warning must be set iff freshness_score fell below the
    threshold — the rule AssembleResponseExecutor applies."""
    response = await pipeline.retrieve(RetrieveRequest(query="stale and fresh clauses", top_k=20))

    assert response.chunks, "corpus produced no results"
    # Stale chunks must trip the warning; fresh ones must not.
    stale = [c for c in response.chunks if c.effective_date == _STALE_DATE]
    fresh = [c for c in response.chunks if c.effective_date == _FRESH_DATE]
    assert fresh, "corpus produced no fresh chunks"
    assert stale, "corpus produced no stale chunks"
    assert all(c.freshness_warning for c in stale)
    assert not any(c.freshness_warning for c in fresh)
    assert _FRESHNESS_WARNING_THRESHOLD == 0.7


async def test_multimodal_metadata_preserved(pipeline):
    """modality / artifact_ref are the multimodal citation metadata the workflow
    passes through from chunk metadata to the response."""
    response = await pipeline.retrieve(RetrieveRequest(query="pricing floor table", top_k=20))

    media = [c for c in response.chunks if c.modality != "text"]
    assert media, "corpus produced no media chunks"
    for chunk in media:
        assert chunk.modality == "table"
        assert chunk.artifact_ref and chunk.artifact_ref.startswith("sha256:")
    # Text chunks must carry no artifact ref.
    assert all(c.artifact_ref is None for c in response.chunks if c.modality == "text")


async def test_parent_expansion_populates_parent_text(pipeline):
    """Small-to-big retrieval: chunks with a parent_chunk_id get parent_text."""
    with_parent = await pipeline.retrieve(
        RetrieveRequest(query="pricing floors section", top_k=20, include_parent=True)
    )
    assert any(c.parent_text for c in with_parent.chunks), "no parent text resolved"


async def test_include_parent_false_suppresses_parent_text(pipeline):
    """The else-branch of ParentExpandExecutor: no expansion, no parent_text."""
    without = await pipeline.retrieve(
        RetrieveRequest(query="pricing floors section", top_k=20, include_parent=False)
    )
    assert all(c.parent_text is None for c in without.chunks)


async def test_reranker_is_invoked_with_top_k(pipeline, fake_reranker):
    """RerankExecutor must call the reranker with the request's top_k, not the
    pre-rerank candidate count."""
    await pipeline.retrieve(RetrieveRequest(query="pricing floor", top_k=4))

    assert fake_reranker.calls, "reranker was never called"
    query, top_n = fake_reranker.calls[-1]
    assert query == "pricing floor"
    assert top_n == 4


async def test_empty_corpus_returns_empty_response(
    settings, fake_embedder, fake_vectordb, fake_reranker
):
    """A query matching nothing must degrade cleanly, not raise."""
    pipeline = RetrievalPipeline(settings, fake_embedder, fake_vectordb)
    response = await pipeline.retrieve(RetrieveRequest(query="a query against an empty corpus"))

    assert response.total_results == 0
    assert response.chunks == []
    assert response.freshness_warning_global is False


async def test_reranker_disabled_falls_back_to_truncation(settings, fake_embedder, fake_vectordb):
    """With no reranker configured, RerankExecutor must truncate the fused list
    to top_k rather than failing."""
    await _seed(fake_vectordb)
    base = settings.model_copy(
        update={
            "embedding": settings.embedding.model_copy(
                update={"provider": EmbeddingProvider.SENTENCE_TRANSFORMER}
            )
        }
    )
    pipeline = RetrievalPipeline(base, fake_embedder, fake_vectordb)
    assert pipeline._reranker is None

    response = await pipeline.retrieve(RetrieveRequest(query="pricing floor", top_k=5))
    assert 0 < len(response.chunks) <= 5


async def test_executor_exception_propagates(
    settings, fake_embedder, fake_vectordb, fake_reranker
):
    """An exception raised inside a stage must propagate out of workflow.run() as
    the original exception.

    This is what keeps the router's try/except returning HTTP 500 and the MCP
    server's returning {"error": ...}. If MAF ever starts capturing executor
    failures as events instead of raising, this test fails and both error
    contracts silently break.
    """
    await _seed(fake_vectordb)

    async def boom(*args, **kwargs):
        raise RuntimeError("vector db down")

    fake_vectordb.dense_search = boom
    pipeline = RetrievalPipeline(settings, fake_embedder, fake_vectordb)

    with pytest.raises(RuntimeError, match="vector db down"):
        await pipeline.retrieve(RetrieveRequest(query="anything at all"))


async def test_stage3_private_attrs_survive(pipeline):
    """eval/stage3_retrieval/runner.py reaches into these to drive the production
    components directly. Removing them would break stage-3 eval silently."""
    assert pipeline._searcher is not None
    assert pipeline._freshness is not None
    assert hasattr(pipeline, "_reranker")
