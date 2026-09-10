"""Behavioural tests for the MAF retrieval workflow.

These replace the migration-era parity suite. That suite proved the workflow
matched the pre-migration inline implementation by running both and diffing; the
inline path no longer exists, so these assert the workflow's contract directly:
ordering, freshness flags, parent expansion, multimodal metadata pass-through,
empty results, reranker fallback, and — most importantly — that an executor's
exception propagates out of ``workflow.run()`` unchanged.
"""

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from gernas_rag.config.embedding import EmbeddingProvider
from gernas_rag.config.retrieval import FRESHNESS_WARNING_THRESHOLD
from gernas_rag.models.chunk import Chunk, ChunkMetadata, EmbeddedChunk, Modality
from gernas_rag.models.retrieval import DocumentFilter, RetrieveRequest
from gernas_rag.retrieval.pipeline import RetrievalPipeline
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


async def test_workflow_is_built_per_request_components_are_not(pipeline):
    """A fresh Workflow per request; the expensive components shared across them.

    This asserts the shape of the fix for the concurrency bug below. MAF refuses a
    second concurrent ``run()`` on one Workflow instance, so caching the Workflow
    on the pipeline — as this class originally did — breaks under any overlapping
    load. What must *not* be rebuilt is the embedder, cross-encoder and vector DB
    client: those load models and hold connections, and rebuilding them per request
    would turn a correctness fix into a performance disaster.
    """
    first = pipeline._build_workflow()
    second = pipeline._build_workflow()
    assert first is not second, "Workflow must not be cached — see _build_workflow's docstring"

    embedder, searcher = pipeline._embedder, pipeline._searcher
    reranker, freshness = pipeline._reranker, pipeline._freshness

    await pipeline.retrieve(RetrieveRequest(query="first call"))
    await pipeline.retrieve(RetrieveRequest(query="second call"))

    assert pipeline._embedder is embedder
    assert pipeline._searcher is searcher
    assert pipeline._reranker is reranker
    assert pipeline._freshness is freshness


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
    assert FRESHNESS_WARNING_THRESHOLD == 0.7


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


class QueryAwareReranker:
    """A reranker whose ordering is a deterministic function of the query.

    ``FakeReranker`` sorts by text length, which is query-blind: every query yields
    the same ranking, so a concurrency test using it would pass even if every
    request returned another request's answer. Character overlap is no better here,
    because this corpus's chunks are near-identical strings.

    So the key is ``sha256(query + chunk_id)``: not a plausible relevance function,
    but it makes each query's ranking distinct and reproducible, which is exactly
    what a contamination test needs. sha256 rather than ``hash()`` because the
    latter is salted per interpreter run and would make failures unreproducible.
    """

    async def rerank(self, query: str, results: list[SearchResult], top_n: int) -> list[SearchResult]:
        def key(r: SearchResult) -> str:
            return hashlib.sha256(f"{query}|{r.chunk_id}".encode()).hexdigest()

        ranked = sorted(results, key=key)[:top_n]
        return [
            SearchResult(r.chunk_id, r.text, r.score, r.metadata, i) for i, r in enumerate(ranked)
        ]


async def test_concurrent_requests_do_not_cross_contaminate(
    monkeypatch, settings, fake_embedder, fake_vectordb
):
    """Overlapping requests must each get their own answer.

    This is the load-bearing safety property of the whole design, and it caught a
    real production bug. The pipeline originally built one ``Workflow`` in
    ``__init__`` and reused it, documented as safe because per-request data travels
    on ``RagState``. MAF disagrees: ``Workflow.run()`` raises

        WorkflowException: Workflow is already running; concurrent runs are not
        allowed on the same instance.

    so the *second* of any two overlapping ``/api/v1/retrieve`` calls failed with
    HTTP 500. Every test and eval ran sequentially, so nothing saw it. The fix is
    a fresh Workflow per request (``RetrievalPipeline._build_workflow``).

    Each query runs twice — alone, then in a concurrent batch — and the two must
    agree. A query-aware reranker makes the rankings genuinely distinct, so a
    swapped or shared state would change the answer rather than go unnoticed.
    """
    monkeypatch.setattr(
        "gernas_rag.retrieval.pipeline.Reranker.from_embedding_config",
        classmethod(lambda cls, cfg: QueryAwareReranker()),
    )
    await _seed(fake_vectordb)
    pipeline = RetrievalPipeline(settings, fake_embedder, fake_vectordb)

    queries = [
        "credit pricing floor by rating",
        "concentration limits by sector",
        "term loan eligibility criteria",
        "model risk escalation thresholds",
        "commitment fee schedule",
        "zzzz",
    ]
    requests = [RetrieveRequest(query=q, top_k=5) for q in queries]

    def fingerprint(response):
        return [(c.source, c.clause_reference, c.score) for c in response.chunks]

    sequential = [fingerprint(await pipeline.retrieve(r)) for r in requests]
    concurrent = [
        fingerprint(r) for r in await asyncio.gather(*(pipeline.retrieve(r) for r in requests))
    ]

    assert concurrent == sequential
    assert all(sequential), "corpus produced no results — the test would be vacuous"
    # Without this, the comparison above would hold trivially even if every request
    # returned another request's answer.
    assert len({tuple(f) for f in sequential}) > 1, "rankings are query-blind; test proves nothing"


async def test_rerank_timeout_degrades_to_fused_order(
    monkeypatch, settings, fake_embedder, fake_vectordb
):
    """A slow cross-encoder must cost ranking quality, not the whole request.

    This mirrors the fallback reranker.py already takes when its model will not
    load — the candidates are still relevant, just in RRF order rather than
    cross-encoder order. Returning a usable answer beats a 500.
    """

    class SlowReranker:
        async def rerank(self, query, results, top_n):
            await asyncio.sleep(5)
            raise AssertionError("should have been cancelled by the timeout")

    monkeypatch.setattr(
        "gernas_rag.retrieval.pipeline.Reranker.from_embedding_config",
        classmethod(lambda cls, cfg: SlowReranker()),
    )
    await _seed(fake_vectordb)
    tuned = settings.model_copy(deep=True)
    tuned.retrieval.rerank_timeout_s = 0.05
    pipeline = RetrievalPipeline(tuned, fake_embedder, fake_vectordb)

    response = await pipeline.retrieve(RetrieveRequest(query="pricing floor", top_k=5))

    assert 0 < len(response.chunks) <= 5
    assert [c.score for c in response.chunks] == sorted(
        (c.score for c in response.chunks), reverse=True
    )


async def test_parent_expand_timeout_degrades_to_no_parents(
    settings, fake_embedder, fake_vectordb, fake_reranker
):
    """parent_text is optional on every chunk, so a slow lookup drops the parents
    and leaves the ranking — the part that matters — intact."""
    await _seed(fake_vectordb)

    async def slow_get_by_ids(ids):
        await asyncio.sleep(5)
        raise AssertionError("should have been cancelled by the timeout")

    fake_vectordb.get_by_ids = slow_get_by_ids
    tuned = settings.model_copy(deep=True)
    tuned.retrieval.parent_expand_timeout_s = 0.05
    pipeline = RetrievalPipeline(tuned, fake_embedder, fake_vectordb)

    response = await pipeline.retrieve(
        RetrieveRequest(query="pricing floor", top_k=5, include_parent=True)
    )

    assert response.chunks, "ranking must survive a parent-expansion timeout"
    assert all(c.parent_text is None for c in response.chunks)


async def test_encode_timeout_propagates(settings, fake_embedder, fake_vectordb, fake_reranker):
    """No safe degradation exists for encode: without a query vector there is
    nothing to search, and an empty result set would read as 'no documents
    matched' — a wrong answer rather than an error."""
    await _seed(fake_vectordb)

    async def slow_embed_query(text):
        await asyncio.sleep(5)

    fake_embedder.embed_query = slow_embed_query
    tuned = settings.model_copy(deep=True)
    tuned.retrieval.encode_timeout_s = 0.05
    pipeline = RetrievalPipeline(tuned, fake_embedder, fake_vectordb)

    with pytest.raises(asyncio.TimeoutError):
        await pipeline.retrieve(RetrieveRequest(query="pricing floor"))


async def test_timeouts_are_off_by_default(settings):
    """Shipping a default would be guessing: this service's own eval takes 90-150 s
    per query on CPU, so any number baked in here would be wrong for someone."""
    assert settings.retrieval.encode_timeout_s is None
    assert settings.retrieval.search_timeout_s is None
    assert settings.retrieval.rerank_timeout_s is None
    assert settings.retrieval.parent_expand_timeout_s is None


async def test_umbrella_agent_framework_is_not_installed():
    """`agent-framework-core` only — never the `agent-framework` umbrella.

    The umbrella resolves to 132 packages and uninstalls 14, downgrading redis
    8.0.0 -> 7.1.1 (this service caches on redis) and major-bumping openai 2.x ->
    3.x, to supply exactly the four names the workflow imports. The reasoning is
    recorded in pyproject.toml; this is the assertion that enforces it, because a
    comment cannot fail a build.

    If this fails, someone widened the dependency. Re-read pyproject.toml before
    changing this test.
    """
    from importlib.metadata import PackageNotFoundError, version

    version("agent-framework-core")  # Raises if the real dependency went missing.

    with pytest.raises(PackageNotFoundError):
        version("agent-framework")
