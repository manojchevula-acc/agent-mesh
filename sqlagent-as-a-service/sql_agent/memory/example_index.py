"""Intent-aware hybrid retrieval over approved few-shot examples (Pattern Retriever).

Mirrors ``semantic_layer/selector.py``: a DENSE ranker (embeddings — semantic/logical
recall, over the question PLUS a short description of the example SQL's query logic —
see ``example_doc_text``) and a SPARSE BM25 ranker (exact banking-jargon / column
precision, over the question alone), fused with WEIGHTED Reciprocal Rank Fusion so a
lexical-but-not-logical match can't out-vote a genuine semantic one (see ``_rrf``). The
fused ranking is then soft-boosted toward examples whose ``tier`` / ``tables`` overlap
the current question's intent, so the generator sees worked examples that touch the
same objects it must query.

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


_TIME_HINTS = ("date", "month", "year", "week", "day", "quarter")


def _parse_shape(sql: str | None) -> dict | None:
    """Parse ``sql`` (sqlglot, a core dependency — see eval/sql_introspect.py for the
    same approach) into the structural facts ``_sql_shape_phrase``/``sql_pattern`` both
    need. Returns None on any parse failure or missing SQL — callers treat that as "no
    signal", never an error."""
    if not sql:
        return None
    try:
        import sqlglot
        from sqlglot import exp

        ast = sqlglot.parse_one(sql, dialect="mysql")
    except Exception:  # noqa: BLE001 — enrichment is best-effort, never fatal
        return None
    if ast is None:
        return None

    group = ast.find(exp.Group)
    where = ast.find(exp.Where)
    order = ast.find(exp.Order)
    has_limit = ast.find(exp.Limit) is not None

    group_cols = sorted({c.name.lower() for c in group.find_all(exp.Column)}) if group else []
    where_cols = sorted({c.name.lower() for c in where.find_all(exp.Column)}) if where else []
    aggs = sorted({type(f).__name__.upper() for f in ast.find_all(exp.AggFunc)})

    if group_cols and any(hint in col for col in group_cols for hint in _TIME_HINTS):
        pattern = "trend"
    elif aggs:
        pattern = "aggregation"
    elif order is not None and has_limit:
        pattern = "ranking"
    else:
        pattern = "lookup"

    return {"pattern": pattern, "aggs": aggs, "group_cols": group_cols,
            "where_cols": where_cols, "has_order": order is not None, "has_limit": has_limit}


def sql_pattern(sql: str | None) -> str:
    """The QUERY LOGIC bucket ``sql`` falls into: ``ranking`` / ``aggregation`` /
    ``trend`` / ``lookup``, or ``""`` if ``sql`` is missing/unparseable. Public so eval
    scripts can compare a gold question's own SQL pattern against a retrieved example's
    (see eval/check_example_retrieval.py) — retrieval quality independent of end-to-end
    SQL accuracy."""
    shape = _parse_shape(sql)
    return shape["pattern"] if shape else ""


def _sql_shape_phrase(sql: str | None) -> str:
    """Best-effort natural-language description of ``sql``'s QUERY LOGIC — ranking vs.
    grouped aggregation vs. a time trend vs. a plain filtered lookup — plus the columns
    it groups/filters/orders by. Fed into the dense embedding alongside the question so
    the vector space clusters examples by what the SQL actually DOES, not just how the
    question happens to be phrased. Never raises: a bad/missing SQL yields "", so a bad
    example never breaks corpus building.
    """
    shape = _parse_shape(sql)
    if shape is None:
        return ""

    bits = [f"Query pattern: {shape['pattern']}."]
    if shape["aggs"]:
        bits.append(f"Aggregates: {', '.join(shape['aggs']).lower()}.")
    if shape["group_cols"]:
        bits.append(f"Grouped by: {', '.join(shape['group_cols'])}.")
    if shape["where_cols"]:
        bits.append(f"Filtered by: {', '.join(shape['where_cols'][:5])}.")
    if shape["has_order"]:
        bits.append("Top-N ranked result." if shape["has_limit"] else "Sorted result.")
    return " ".join(bits)


def example_doc_text(row: dict) -> str:
    """Text embedded into the DENSE index for one example: its glossary-expanded
    question plus a short structural description of its SQL's query logic. Used by both
    the runtime corpus build (below) and the offline scripts/build_example_index.py, so
    an offline-built index never drifts from what the live retriever expects."""
    from sql_agent.semantic_layer.catalog import glossary_expand

    text = glossary_expand(row.get("question", ""))
    shape = _sql_shape_phrase(row.get("validated_sql"))
    return f"{text}\n{shape}" if shape else text


def _corpus_signature(rows: list[dict]) -> tuple:
    return tuple(r.get("question", "") for r in rows)


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

        fused = _rrf(
            [(dense_rank, settings.examples_dense_weight),
             (sparse_rank, settings.examples_bm25_weight)],
            settings.rrf_k,
        )

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
