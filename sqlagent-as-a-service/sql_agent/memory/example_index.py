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

import json

from sql_agent.config import settings
from sql_agent.logging_config import get_logger
from sql_agent.memory.sql_pattern import shape_phrase, sql_pattern  # noqa: F401 — re-export

log = get_logger("examples")

# Module-level cache of the built dense/sparse indices, keyed by a signature of the
# approved-example corpus (questions + metadata + the scale-mode flags, since either
# changes what gets embedded/stored). Rebuilt only when that signature changes.
# ``dense_ok`` records whether the dense VectorIndex was populated; ``name_to_idx`` maps a
# stored vector's payload name (the example question) back to its row index.
_CACHE: dict = {"sig": None, "names": None, "bm25": None,
                "name_to_idx": None, "dense_ok": False}

# Per-question metadata cache for approved examples whose stored ``metadata`` column is
# missing/blank (rows seeded before the metadata feature, or a test fixture) — computed
# once via example_metadata.generate rather than on every retrieval. The corpus is small
# (tens of rows) so this never grows unbounded in practice.
_METADATA_CACHE: dict[str, dict] = {}


def row_metadata(row: dict) -> dict:
    """The structured metadata dict for one example row: the stored ``metadata`` JSON
    when present, else generated on the fly from question+SQL (and cached). Shared by
    the ranker's overlap factors, the payload build below, and the offline index
    builder — one reader, no drift."""
    raw = row.get("metadata")
    if raw:
        try:
            return json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:  # noqa: BLE001 — fall through to on-the-fly generation
            pass
    question = row.get("question", "")
    cached = _METADATA_CACHE.get(question)
    if cached is not None:
        return cached
    from sql_agent.memory.example_metadata import generate

    meta = generate(question, row.get("validated_sql"))
    _METADATA_CACHE[question] = meta
    return meta


def _structure_suffix(intent: str | None, patterns) -> str:
    """One short sentence of structural vocabulary ("Query pattern: … Intent: …"),
    shared by the query side (from the rule tagger) and the example side (from stored
    metadata) when ``examples_structured_query_enabled`` — deliberately brief so the
    question text still dominates the vector and a tagger error can only nudge it."""
    bits = []
    if patterns:
        bits.append(f"Query pattern: {', '.join(sorted(patterns))}.")
    if intent:
        bits.append(f"Intent: {intent}.")
    return " ".join(bits)


def example_doc_text(row: dict) -> str:
    """Text embedded into the DENSE index for one example: its glossary-expanded
    question plus a short structural description of its SQL's query logic. Used by both
    the runtime corpus build (below) and the offline scripts/build_example_index.py, so
    an offline-built index never drifts from what the live retriever expects.

    With ``examples_structured_query_enabled`` the stored metadata's intent joins the
    text too, mirroring ``query_doc_text`` so query and example vectors share the same
    structural vocabulary (the SQL-shape phrase already carries the pattern tokens)."""
    from sql_agent.semantic_layer.catalog import glossary_expand

    text = glossary_expand(row.get("question", ""))
    shape = shape_phrase(row.get("validated_sql"))
    if settings.examples_structured_query_enabled:
        intent = row_metadata(row).get("intent")
        extra = _structure_suffix(intent, None)
        shape = f"{shape} {extra}".strip() if extra else shape
    return f"{text}\n{shape}" if shape else text


def query_doc_text(question: str, intent: str | None = None, patterns=None) -> str:
    """Text the LIVE question is embedded/BM25-matched as. Baseline (flag off): the
    glossary-expanded question — byte-identical to prior behaviour. With
    ``examples_structured_query_enabled``: plus the same one-sentence structural suffix
    ``example_doc_text`` uses, built from the rule tagger's intent/expected-patterns
    (the live question has no SQL yet), so both sides meet in the same subspace."""
    from sql_agent.semantic_layer.catalog import glossary_expand

    text = glossary_expand(question)
    if not settings.examples_structured_query_enabled:
        return text
    suffix = _structure_suffix(intent, patterns)
    return f"{text}\n{suffix}" if suffix else text


def _corpus_signature(rows: list[dict]) -> tuple:
    # Flags + metadata are part of the signature: toggling a scale-mode flag or editing
    # an example's metadata changes what gets embedded/stored, so the in-process index
    # must rebuild (a persisted qdrant collection additionally needs --force, see
    # scripts/build_example_index.py).
    return (
        settings.examples_prefilter_enabled,
        settings.examples_structured_query_enabled,
        tuple((r.get("question", ""), str(r.get("metadata") or "")) for r in rows),
    )


def ensure_built(rows: list[dict]) -> None:
    """(Re)build the dense/BM25 indices over ``rows`` if the corpus has changed since
    the last build. Cheap no-op otherwise — safe to call on every retrieval."""
    if _CACHE["sig"] != _corpus_signature(rows):
        _build(rows)


def example_payloads(rows: list[dict]) -> dict[str, dict]:
    """Per-example vector-store payloads keyed by question: the fields pre-filtered
    retrieval conditions on (``tables`` via {"any": ...}, ``intent``) plus ``tier`` for
    inspection. Shared by the runtime build below and scripts/build_example_index.py."""
    payloads: dict[str, dict] = {}
    for r in rows:
        meta = row_metadata(r)
        payloads[r.get("question", "")] = {
            "tables": list(meta.get("tables") or []),
            "intent": meta.get("intent", ""),
            "tier": r.get("tier", ""),
        }
    return payloads


def _build(rows: list[dict]) -> None:
    """(Re)build the dense VectorIndex + BM25 index over the approved examples.

    DENSE embeds ``example_doc_text`` (question + SQL query-logic shape) — semantic
    recall over what each example actually teaches — and stores each example's
    tables/intent as PAYLOAD so pre-filtered retrieval can condition on them. BM25
    stays on the glossary-expanded QUESTION (exact banking-jargon/column precision);
    with ``examples_structured_query_enabled`` the metadata's intent/pattern tokens
    join the BM25 docs too, since the enriched query side can now lexically match them.
    """
    from sql_agent.semantic_layer.catalog import glossary_expand

    docs = [example_doc_text(r) for r in rows]
    questions = [glossary_expand(r.get("question", "")) for r in rows]
    if settings.examples_structured_query_enabled:
        questions = [
            f"{q} {_structure_suffix(row_metadata(r).get('intent'), row_metadata(r).get('sql_pattern'))}".strip()
            for q, r in zip(questions, rows)
        ]
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
            # After EDITING the seed set, changing metadata, or toggling a scale-mode
            # flag, refresh a persisted qdrant collection with
            # scripts/build_example_index.py --force.
            index.build(names, backend.embed(docs), payloads=example_payloads(rows))
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


def dense_scores(question: str, where: dict | None = None,
                 top_m: int | None = None) -> dict[int, float]:
    """Cosine similarity of ``question`` to examples (row index -> score).

    Default (no ``where``): full-corpus ranking, exactly as before. With ``where`` /
    ``top_m`` (pre-filtered scale mode): the vector store returns at most ``top_m``
    candidates matching the payload filter — examples outside the filter are never
    scored. Returns {} when the dense backend/index is unavailable — the confidence
    gate then can't fire and retrieval falls back to BM25-only ranking without a
    threshold. Assumes ``ensure_built`` has already run.
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
        k = min(top_m, len(n2i)) if top_m else len(n2i)
        hits = get_example_vector_index().search(qv, k, where=where)
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


def semantic_signal(
    question: str,
    rows: list[dict],
    *,
    live_tables=None,
    intent: str | None = None,
    patterns=None,
) -> tuple[dict[int, float], dict[int, float], bool]:
    """The "semantic" term for ``example_ranker.py``'s weighted score: hybrid dense+BM25
    fused via weighted RRF, PLUS the raw dense cosine scores the confidence gate needs.

    ``live_tables``/``intent``/``patterns`` are the question's pre-derived structured
    signals (the "retrieval key"). With the scale-mode flags on they change HOW the
    fetch runs — ``examples_structured_query_enabled`` enriches the embedded/BM25 query
    text (``query_doc_text``), ``examples_prefilter_enabled`` pushes a
    ``{"tables": {"any": ...}}`` payload filter + bounded ``top_m`` into the vector
    store — and with both off they are ignored, keeping behaviour byte-identical.

    Returns ``(fused_rrf_scores, raw_dense_scores, prefiltered)``. ``prefiltered`` is
    True only when the store-side table filter actually constrained the dense fetch —
    the caller can then skip its redundant Python-side table filter. A filtered fetch
    that comes back EMPTY is retried once unfiltered (never-starve contract), with
    ``prefiltered`` False. ``fused_rrf_scores`` is ``{}`` when NEITHER ranker is
    available (caller should fall back to a static slice); ``raw_dense_scores`` is
    ``{}`` whenever the dense backend is off, even if BM25 alone produced a ranking.
    """
    ensure_built(rows)

    qtext = query_doc_text(question, intent=intent, patterns=patterns)

    where = None
    top_m = None
    if settings.examples_prefilter_enabled and live_tables:
        where = {"tables": {"any": sorted(live_tables)}}
        top_m = settings.examples_search_top_m

    d_scores = dense_scores(qtext, where=where, top_m=top_m)
    prefiltered = bool(where) and bool(d_scores)
    if where and not d_scores:
        # Over-narrow filter, payload-less collection (pre-rebuild), or a filter-blind
        # backend returning nothing: retry unfiltered so the filter can never starve
        # generation. The ranker's Python-side filter still applies downstream.
        log.info("PATTERN prefilter empty | tables=%s | retrying unfiltered",
                 sorted(live_tables))
        d_scores = dense_scores(qtext)

    dense_rank = sorted(d_scores, key=d_scores.get, reverse=True)
    sparse_rank = _sparse_ranking(qtext)
    if prefiltered:
        # Keep RRF's two rankings over the SAME candidate universe: mask BM25 to the
        # store-filtered set (BM25 is in-process and cannot pre-filter itself).
        eligible = set(d_scores)
        sparse_rank = [i for i in sparse_rank if i in eligible]
    if not dense_rank and not sparse_rank:
        return {}, d_scores, False
    fused = _rrf(
        [(dense_rank, settings.examples_dense_weight),
         (sparse_rank, settings.examples_bm25_weight)],
        settings.rrf_k,
    )
    return fused, d_scores, prefiltered


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
