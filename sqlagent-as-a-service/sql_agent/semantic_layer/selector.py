"""Select the minimal relevant tables for a dynamic-tier question (Component B).

Hybrid retrieval: a DENSE ranker (embeddings — semantic / synonym recall) and a SPARSE
BM25 ranker (exact column/enum/entity precision, rare finance jargon), over a
glossary-expanded question, fused with Reciprocal Rank Fusion. Top-K is taken, any
``tables_hint`` from intent is force-included, then deterministic join-closure is applied
so relationships needed for a JOIN are never dropped.

Recall-oriented on purpose: this returns CANDIDATES. The schema-link planner
(schema_link.py) makes the precise decision. Retrieval only narrows what the generator
SEES; the validator's allow-list is unchanged. Falls back to the full table set when the
feature is disabled or retrieval yields nothing, so generation is never starved.

Heavy imports (rank_bm25 / numpy / embeddings backend) are lazy so the module imports
cleanly without the optional `retrieval` extra when the feature flag is off.
"""

from __future__ import annotations

from functools import lru_cache

from sql_agent.config import settings
from sql_agent.logging_config import get_logger
from sql_agent.semantic_layer.catalog import glossary_expand
from sql_agent.semantic_layer.loader import (
    ALLOWED_TABLES,
    base_join_closure,
    join_closure,
    load_semantic_layer,
)

log = get_logger("selector")


def _table_docs() -> dict[str, str]:
    """Rich per-table document: name + grain + column names + descriptions + enum values.
    Dense recall on schema is driven mostly by column descriptions, so include them."""
    layer = load_semantic_layer()
    docs: dict[str, str] = {}
    for name, table in layer.tables.items():
        cols = " ".join(
            f"{c.name} {c.desc} {' '.join(c.values)}" for c in table.columns.values()
        )
        # search_terms carries NL trigger phrases/synonyms for the view's business
        # question archetypes — folded into the retrieval doc so a user phrasing like
        # "explain how the price was calculated" recalls pricing_trace_view even though
        # those words aren't in the grain/column text.
        docs[name] = f"{name} {table.grain} {table.search_terms} {cols}"
    return docs


@lru_cache(maxsize=1)
def _dense_index():
    """The built VectorIndex for the table documents, or None when dense is off.
    The index backend (memory / faiss / qdrant) is chosen by config; this only decides
    WHAT to index (the table documents) and delegates storage/search to the backend."""
    from sql_agent.semantic_layer.embeddings import get_backend
    from sql_agent.semantic_layer.vector_index import get_vector_index

    backend = get_backend()
    if backend is None:
        return None
    docs = _table_docs()
    names = list(docs)
    index = get_vector_index()
    index.build(names, backend.embed([docs[n] for n in names]))
    return index


@lru_cache(maxsize=1)
def _bm25_index():
    from rank_bm25 import BM25Okapi

    docs = _table_docs()
    names = list(docs)
    corpus = [docs[n].lower().split() for n in names]
    return BM25Okapi(corpus), names


def _dense_ranking(question: str) -> list[str]:
    from sql_agent.semantic_layer.embeddings import get_backend

    index = _dense_index()
    if index is None:
        return []
    qv = get_backend().embed_query(question)  # bge query-prefix applied here
    # Request a generous candidate pool so RRF has enough dense signal to fuse.
    k = max(settings.embedding_top_k * 3, 25)
    return [name for name, _ in index.search(qv, k)]


def _sparse_ranking(question: str) -> list[str]:
    bm25, names = _bm25_index()
    scores = bm25.get_scores(question.lower().split())
    order = scores.argsort()[::-1]
    return [names[i] for i in order]


def _rrf(rankings: list[list[str]], k: int) -> list[str]:
    """Reciprocal Rank Fusion: score(t) = Σ 1 / (k + rank_in_each_ranking)."""
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, table in enumerate(ranking):
            fused[table] = fused.get(table, 0.0) + 1.0 / (k + rank)
    return sorted(fused, key=lambda t: fused[t], reverse=True)


def select_tables(
    question: str, tables_hint: list[str] | None = None, top_k: int | None = None,
    apply_closure: bool = True,
) -> set[str]:
    """Return the candidate table set for a dynamic-tier question.

    ``top_k`` overrides ``settings.embedding_top_k`` for this call only — used by the
    widen-and-retry path (query_engine.py) to pull a bigger-but-still-bounded slice of
    the SAME already-computed fused ranking, instead of falling back to literally every
    table in the schema (which is the single largest, most expensive prompt component
    and was blowing the provider's per-minute token budget on retries).

    ``apply_closure`` adds tables reachable via one declared join hop (bridge tables) so a
    join is never missing from the RENDERED schema. The schema-link PLANNER passes False:
    it only needs to pick among the ranked candidates, and rendering the full join closure
    (customer_master neighbours almost every view) can blow the provider's per-minute token
    limit. resolve_joins re-adds any bridge the plan actually needs afterwards, so the final
    generation schema is unaffected."""
    if not settings.schema_retrieval_enabled:
        return set(ALLOWED_TABLES)  # current behaviour: full schema

    k = top_k if top_k is not None else settings.embedding_top_k

    try:
        q = glossary_expand(question)
        fused = _rrf([_dense_ranking(q), _sparse_ranking(q)], settings.rrf_k)
    except Exception as exc:  # noqa: BLE001 — retrieval must never break the turn
        log.warning("retrieval failed | %s | falling back to full schema", exc)
        return set(ALLOWED_TABLES)

    # Core candidates = the top-K by fused RRF rank (kept in rank order, best first),
    # plus any tables the intent classifier hinted.
    core = [t for t in fused if t in ALLOWED_TABLES][:k]
    for t in tables_hint or []:
        if t in ALLOWED_TABLES and t not in core:
            core.append(t)
    if not core:
        return set(ALLOWED_TABLES)  # safe fallback: never starve generation

    if not apply_closure:
        # Planner path: ranked candidates PLUS a BASE-table-only join closure. The full
        # view-inclusive closure is still withheld here (it pulls customer_master's dozen
        # view neighbours and blows the per-minute token limit), but the base bridges it
        # would have added — product_master, treasury_rate_sheet, and customer_master as
        # the bridge into pricing_policy — are exactly the tables the deal-grain views
        # crowd out of the ranking, leaving a multi-base-table join unbuildable. Adding
        # only base neighbours (at most the five base tables) keeps the planner prompt
        # small while making those joins reachable. resolve_joins still prunes to the
        # minimal path the plan actually uses.
        closed = base_join_closure(set(core))
        added = [t for t in sorted(closed) if t not in core]
        log.info("RETRIEVE ranked=%s +base_closure=%s (planner)", core, added)
        return closed

    # Join-closure adds tables needed to JOIN the core ones — recall safety, NOT priority.
    closed = join_closure(set(core))
    added = [t for t in sorted(closed) if t not in core]
    # Log core in RANK ORDER (most relevant first); closure additions listed separately.
    log.info("RETRIEVE ranked=%s +join_closure=%s", core, added)
    return closed
