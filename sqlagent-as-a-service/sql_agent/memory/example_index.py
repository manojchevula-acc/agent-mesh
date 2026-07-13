"""Intent-aware hybrid retrieval over approved few-shot examples (Pattern Retriever).

Mirrors ``semantic_layer/selector.py``: a DENSE ranker (embeddings — semantic recall)
and a SPARSE BM25 ranker (exact banking-jargon / column precision) over the approved
examples' QUESTION text, fused with Reciprocal Rank Fusion. The fused ranking is then
soft-boosted toward examples whose ``tier`` / ``tables`` overlap the current question's
intent, so the generator sees worked examples that touch the same objects it must query.

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

log = get_logger("examples")

# Module-level cache of the built dense/sparse indices, keyed by a signature of the
# approved-example corpus (its questions). Rebuilt only when the corpus changes.
# ``dense_ok`` records whether the dense VectorIndex was populated; ``name_to_idx`` maps a
# stored vector's payload name (the example question) back to its row index.
_CACHE: dict = {"sig": None, "names": None, "bm25": None,
                "name_to_idx": None, "dense_ok": False}


def _corpus_signature(rows: list[dict]) -> tuple:
    return tuple(r.get("question", "") for r in rows)


def _build(rows: list[dict]) -> None:
    """(Re)build the dense VectorIndex + BM25 index over the example questions."""
    from sql_agent.semantic_layer.catalog import glossary_expand

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
            index.build(names, backend.embed(questions))
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


def _dense_scores(question: str) -> dict[int, float]:
    """Cosine similarity of ``question`` to every example (row index -> score).

    Queries the dense VectorIndex for the whole corpus (small) and maps each returned
    payload name back to its row index. Returns {} when the dense backend/index is
    unavailable — the confidence gate then can't fire and retrieval falls back to
    BM25-only ranking without a threshold.
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
        qv = backend.embed_query(question)  # bge query-prefix applied here
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


def _rrf(rankings: list[list[int]], k: int) -> dict[int, float]:
    """Reciprocal Rank Fusion: score(i) = Σ 1 / (k + rank_in_each_ranking)."""
    fused: dict[int, float] = {}
    for ranking in rankings:
        for rank, idx in enumerate(ranking):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (k + rank)
    return fused


def _example_tables(row: dict) -> set[str]:
    return {t.strip() for t in (row.get("tags") or "").split(",") if t.strip()}


def rank_examples(
    question: str,
    rows: list[dict],
    tier: str | None = None,
    tables_hint: list[str] | None = None,
    k: int | None = None,
) -> list[dict]:
    """Return the top-``k`` most relevant example rows for ``question``.

    Hybrid dense+BM25 RRF, then a soft boost for examples whose ``tier`` matches or whose
    tagged tables overlap ``tables_hint`` (the intent signal already computed upstream).
    Never raises: on any failure it returns a static head slice so generation is never
    starved of (or broken by) examples.
    """
    if not rows:
        return []
    k = k or settings.examples_top_k

    try:
        if _CACHE["sig"] != _corpus_signature(rows):
            _build(rows)

        from sql_agent.semantic_layer.catalog import glossary_expand

        expanded = glossary_expand(question)
        dense_scores = _dense_scores(expanded)  # {idx: cosine} or {} if dense is off
        dense_rank = sorted(dense_scores, key=dense_scores.get, reverse=True)
        sparse_rank = _sparse_ranking(expanded)
        if not dense_rank and not sparse_rank:  # no rankers available -> static head slice
            return rows[:k]

        fused = _rrf([dense_rank, sparse_rank], settings.rrf_k)

        # Confidence gate: when a dense signal is available, drop examples whose cosine
        # similarity to the question is below the floor. If NOTHING clears the floor, inject
        # no examples at all (schema-only generation, today's baseline) rather than a
        # misleading one — this makes the "question not in the example set" case provably
        # safe. Disabled (min_score <= 0) => today's behaviour. No dense signal => no gate.
        min_score = settings.examples_min_score
        if dense_scores and min_score > 0:
            fused = {i: sc for i, sc in fused.items()
                     if dense_scores.get(i, 0.0) >= min_score}
            if not fused:
                best = max(dense_scores.values())
                log.info("PATTERN retrieve | %d examples | best score %.3f < floor %.2f "
                         "| no examples injected", len(rows), best, min_score)
                return []

        # Soft intent boost: nudge (never hard-filter) examples that touch the same
        # tables / tier as the live question. Bonus is scaled to the RRF magnitude so it
        # re-orders near-ties without overriding a clearly stronger textual match.
        hint = {t for t in (tables_hint or []) if t}
        max_score = max(fused.values())
        for idx, row in enumerate(rows):
            if idx not in fused:
                continue
            if hint and (_example_tables(row) & hint):
                fused[idx] += 0.5 * max_score
            if tier and row.get("tier") == tier:
                fused[idx] += 0.1 * max_score

        ordered = sorted(fused, key=lambda i: fused[i], reverse=True)
        top = [rows[i] for i in ordered[:k]]
        log.info("PATTERN retrieve | %d examples | picked=%s",
                 len(rows), [r.get("question", "")[:48] for r in top])
        return top
    except Exception as exc:  # noqa: BLE001 — retrieval must never break the turn
        log.warning("example retrieval failed | %s | static head slice", exc)
        return rows[:k]
