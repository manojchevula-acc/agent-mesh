"""Semantic (dense+BM25) retrieval layer for approved few-shot examples (Pattern
Retriever) — the "semantic similarity" term of the weighted score in
``memory/example_ranker.py``.

Mirrors ``semantic_layer/selector.py``: a DENSE ranker (embeddings — semantic/logical
recall, over the question PLUS a short description of the example SQL's query logic —
see ``example_doc_text``, built from ``memory/sql_pattern.py``) and a SPARSE BM25
ranker (exact banking-jargon / column precision, over the question alone), fused with
WEIGHTED Reciprocal Rank Fusion so a lexical-but-not-logical match can't out-vote a
genuine semantic one (see ``_rrf``).

This module owns ONLY the semantic signal and the corpus cache. Metadata-aware
filtering, the multi-factor weighted score (table/column/intent/pattern/join overlap),
and diversity re-ranking live in ``example_ranker.py`` — ``rank_examples`` below is a
thin backward-compatible delegate so existing callers (``memory/examples.py``,
``eval/check_example_retrieval.py``) don't need to change their import.

The DENSE side is stored through the swappable ``VectorIndex`` abstraction
(``get_example_vector_index``): ``memory`` (in-RAM cosine, default for small corpora),
``faiss``, or ``qdrant`` (a SEPARATE ``sql_agent_examples`` collection, sharing the schema
index's client/connection). BM25 stays in-process. Reuses the SAME embedding backend as
schema retrieval; example vectors are NEVER mixed into the schema collection. Retrieval
must never break the turn: any failure (missing ``retrieval`` extra, embedding/index
error, empty corpus) degrades gracefully to a static head-of-list slice.

Heavy imports (rank_bm25 / numpy / embeddings / vector index) are lazy so this module
imports cleanly without the optional ``retrieval`` extra when the feature flag is off.
"""

from __future__ import annotations

from sql_agent.config import settings
from sql_agent.logging_config import get_logger
from sql_agent.memory.sql_pattern import shape_phrase, sql_pattern  # noqa: F401 — re-export

log = get_logger("examples")

# Module-level cache of the built dense/sparse indices, keyed by a signature of the
# approved-example corpus (its questions). Rebuilt only when the corpus changes.
# ``dense_ok`` records whether the dense VectorIndex was populated; ``name_to_idx`` maps a
# stored vector's payload name (the example question) back to its row index.
_CACHE: dict = {"sig": None, "names": None, "bm25": None,
                "name_to_idx": None, "dense_ok": False}


def example_doc_text(row: dict) -> str:
    """Text embedded into the DENSE index for one example: its glossary-expanded
    question plus a short structural description of its SQL's query logic. Used by both
    the runtime corpus build (below) and the offline scripts/build_example_index.py, so
    an offline-built index never drifts from what the live retriever expects."""
    from sql_agent.semantic_layer.catalog import glossary_expand

    text = glossary_expand(row.get("question", ""))
    shape = shape_phrase(row.get("validated_sql"))
    return f"{text}\n{shape}" if shape else text


def _corpus_signature(rows: list[dict]) -> tuple:
    return tuple(r.get("question", "") for r in rows)


def ensure_built(rows: list[dict]) -> None:
    """(Re)build the dense/BM25 indices over ``rows`` if the corpus has changed since
    the last build. Cheap no-op otherwise — safe to call on every retrieval."""
    if _CACHE["sig"] != _corpus_signature(rows):
        _build(rows)


def _build(rows: list[dict]) -> None:
    """(Re)build the dense VectorIndex + BM25 index over the approved examples.

    DENSE embeds ``example_doc_text`` (question + SQL query-logic shape) — semantic
    recall over what each example actually teaches. BM25 stays on the raw
    glossary-expanded QUESTION only — it is the exact banking-jargon/column-name
    precision specialist and would gain nothing from structural phrases the live
    question (which has no SQL yet) could never lexically match anyway.
    """
    from sql_agent.semantic_layer.catalog import glossary_expand

    docs = [example_doc_text(r) for r in rows]
    questions = [glossary_expand(r.get("question", "")) for r in rows]
    names = [r.get("question", "") for r in rows]  # stable, unique payload key

    dense_ok = False
    try:
        from sql_agent.semantic_layer.embeddings import get_backend

        backend = get_backend()
        if backend is not None:
            from sql_agent.semantic_layer.vector_index import get_example_vector_index

            index = get_example_vector_index()
            # Idempotent for qdrant (skips upsert when already populated, so vectors
            # persist across restarts); a full in-process (re)build for memory/faiss.
            # After EDITING the seed set, refresh a persisted qdrant collection with
            # scripts/build_example_index.py --force.
            index.build(names, backend.embed(docs))
            dense_ok = True
    except Exception as exc:  # noqa: BLE001 — dense is optional; fall back to BM25.
        log.warning("example dense index build failed | %s | BM25-only", exc)
        dense_ok = False

    bm25 = None
    try:
        from rank_bm25 import BM25Okapi

        bm25 = BM25Okapi([q.lower().split() for q in questions])
    except Exception as exc:  # noqa: BLE001 — sparse is optional; fall back to dense.
        log.warning("example BM25 index build failed | %s | dense-only", exc)
        bm25 = None

    _CACHE.update(sig=_corpus_signature(rows), names=names, bm25=bm25,
                  name_to_idx={n: i for i, n in enumerate(names)}, dense_ok=dense_ok)


def dense_scores(question: str) -> dict[int, float]:
    """Cosine similarity of ``question`` to every example (row index -> score).

    Queries the dense VectorIndex for the whole corpus (small) and maps each returned
    payload name back to its row index. Returns {} when the dense backend/index is
    unavailable — the confidence gate then can't fire and retrieval falls back to
    BM25-only ranking without a threshold. Assumes ``ensure_built`` has already run.
    """
    if not _CACHE.get("dense_ok"):
        return {}
    try:
        from sql_agent.semantic_layer.embeddings import get_backend
        from sql_agent.semantic_layer.vector_index import get_example_vector_index

        backend = get_backend()
        if backend is None:
            return {}
        n2i = _CACHE["name_to_idx"] or {}
        # Example-specific bge query-prefix (NOT the schema-retrieval one) — see
        # settings.examples_embedding_query_prefix.
        qv = backend.embed_query(question, prefix=settings.examples_embedding_query_prefix)
        hits = get_example_vector_index().search(qv, len(n2i))  # full ranking + scores
        return {n2i[name]: float(score) for name, score in hits if name in n2i}
    except Exception as exc:  # noqa: BLE001 — dense is optional; fall back to BM25.
        log.warning("example dense search failed | %s | BM25-only", exc)
        return {}


def _sparse_ranking(question: str) -> list[int]:
    """Row indices ordered by BM25 score for ``question`` (best first)."""
    if _CACHE["bm25"] is None:
        return []
    scores = _CACHE["bm25"].get_scores(question.lower().split())
    return list(scores.argsort()[::-1])


def _rrf(rankings: list[tuple[list[int], float]], k: int) -> dict[int, float]:
    """Weighted Reciprocal Rank Fusion: score(i) = Σ weight / (k + rank_in_each_ranking).

    Plain RRF gives every ranking an equal vote regardless of how strong its signal
    actually is — a BM25 top-1 keyword match and a middling dense rank would contribute
    comparably, letting lexical overlap out-vote genuine semantic similarity. Weighting
    (examples_dense_weight / examples_bm25_weight) fixes that while keeping graceful
    degradation: a ranking passed as ``[]`` (its ranker unavailable) contributes nothing,
    so the other ranking's relative ORDER is unaffected by the weight (a constant per-list
    scale factor doesn't reorder a single list).
    """
    fused: dict[int, float] = {}
    for ranking, weight in rankings:
        for rank, idx in enumerate(ranking):
            fused[idx] = fused.get(idx, 0.0) + weight / (k + rank)
    return fused


def semantic_signal(question: str, rows: list[dict]) -> tuple[dict[int, float], dict[int, float]]:
    """The "semantic" term for ``example_ranker.py``'s weighted score: hybrid dense+BM25
    fused via weighted RRF, PLUS the raw dense cosine scores the confidence gate needs.

    Returns ``(fused_rrf_scores, raw_dense_scores)``, both ``{idx: score}``. Rebuilds
    the corpus index first if ``rows`` changed since the last call. ``fused_rrf_scores``
    is ``{}`` when NEITHER ranker is available (caller should fall back to a static
    slice); ``raw_dense_scores`` is ``{}`` whenever the dense backend is off, even if
    BM25 alone produced a fused ranking.
    """
    ensure_built(rows)
    from sql_agent.semantic_layer.catalog import glossary_expand

    expanded = glossary_expand(question)
    d_scores = dense_scores(expanded)
    dense_rank = sorted(d_scores, key=d_scores.get, reverse=True)
    sparse_rank = _sparse_ranking(expanded)
    if not dense_rank and not sparse_rank:
        return {}, d_scores
    fused = _rrf(
        [(dense_rank, settings.examples_dense_weight),
         (sparse_rank, settings.examples_bm25_weight)],
        settings.rrf_k,
    )
    return fused, d_scores


def _example_tables(row: dict) -> set[str]:
    return {t.strip() for t in (row.get("tags") or "").split(",") if t.strip()}


def rank_examples(
    question: str,
    rows: list[dict],
    tier: str | None = None,
    tables_hint: list[str] | None = None,
    k: int | None = None,
) -> list[dict]:
    """Backward-compatible delegate to ``example_ranker.rank_examples`` (Phases 4/8-10:
    metadata-filtered candidates, weighted multi-factor score, diversity re-ranking).
    Kept here so existing imports (``memory/examples.py``, the eval script) don't need
    to change. See ``example_ranker.py`` for the full algorithm."""
    from sql_agent.memory.example_ranker import rank_examples as _rank_examples

    return _rank_examples(question, rows, tier=tier, tables_hint=tables_hint, k=k)
