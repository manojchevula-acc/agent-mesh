"""Unit tests for the retrieval pipeline (hybrid search, RRF, freshness)."""

from datetime import datetime, timedelta, timezone

from gernas_rag.config.retrieval import RetrievalConfig
from gernas_rag.retrieval.freshness import FreshnessFilter
from gernas_rag.retrieval.hybrid_search import HybridSearcher
from gernas_rag.vectordb.base import SearchResult


def _sr(chunk_id: str, text: str, score: float, **meta) -> SearchResult:
    return SearchResult(chunk_id, text, score, dict(meta), 0)


def test_rrf_merge_combines_and_dedups(fake_vectordb):
    searcher = HybridSearcher(RetrievalConfig(rrf_k=60), fake_vectordb)
    dense = [_sr("a", "A", 0.9), _sr("b", "B", 0.8)]
    sparse = [_sr("b", "B", 0.7), _sr("c", "C", 0.6)]
    merged = searcher._rrf_merge(dense, sparse, top_k=10)
    ids = [r.chunk_id for r in merged]
    assert set(ids) == {"a", "b", "c"}
    # 'b' appears in both lists, so it should rank first.
    assert ids[0] == "b"


def test_rrf_respects_top_k(fake_vectordb):
    searcher = HybridSearcher(RetrievalConfig(), fake_vectordb)
    dense = [_sr(str(i), str(i), 1.0 - i * 0.1) for i in range(5)]
    merged = searcher._rrf_merge(dense, [], top_k=3)
    assert len(merged) == 3


async def test_hybrid_search_runs_dense_and_sparse(fake_vectordb, sample_chunk):
    from gernas_rag.models.chunk import EmbeddedChunk

    await fake_vectordb.upsert(
        [EmbeddedChunk(chunk=sample_chunk, dense_vector=[0.1] * 8, sparse_indices=[1], sparse_values=[0.5])]
    )
    searcher = HybridSearcher(RetrievalConfig(), fake_vectordb)
    results = await searcher.search([0.1] * 8, [1], [0.5], None, pre_rerank_top_k=5)
    assert results


def test_freshness_penalises_stale_chunks():
    config = RetrievalConfig(freshness_penalty_enabled=True, freshness_max_age_days=180)
    f = FreshnessFilter(config)
    old_date = (datetime.now(timezone.utc) - timedelta(days=720)).strftime("%Y-%m-%d")
    fresh_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    results = [
        _sr("old", "old", 1.0, effective_date=old_date),
        _sr("fresh", "fresh", 0.95, effective_date=fresh_date),
    ]
    out = f.apply(results)
    by_id = {r.chunk_id: r for r in out}
    assert by_id["old"].metadata["freshness_score"] < 1.0
    assert by_id["fresh"].metadata["freshness_score"] == 1.0
    # Stale chunk is penalised below the fresh one despite equal/higher base score.
    assert by_id["fresh"].score >= by_id["old"].score


def test_freshness_disabled_is_noop():
    config = RetrievalConfig(freshness_penalty_enabled=False)
    f = FreshnessFilter(config)
    results = [_sr("a", "a", 1.0, effective_date="2000-01-01")]
    out = f.apply(results)
    assert out[0].score == 1.0
