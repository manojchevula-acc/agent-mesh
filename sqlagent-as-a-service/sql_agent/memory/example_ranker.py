"""Metadata-aware hybrid retrieval + re-ranking for few-shot examples (Phases 4, 8-10).

Orchestrates the full Pattern Retriever pipeline on top of the existing semantic
(dense+BM25 RRF) signal in ``example_index.py``:

  1. METADATA FILTER  (Phase 4) — restrict candidates to examples whose tables overlap
     the question's schema-retrieval-selected tables; fall back to the full corpus if
     that intersection is empty (never starve generation, same philosophy as every
     other retrieval stage in this codebase).
  2. CANDIDATE POOL    (Phase 10) — take the top ``examples_candidate_pool_k`` of the
     filtered set by the existing semantic ranking (cheap, already computed).
  3. WEIGHTED SCORE    (Phase 8) — combine semantic similarity with table/column/
     intent/pattern/join overlap, weights from ``settings`` (not hardcoded).
  4. DIVERSITY RE-RANK (Phase 9) — an MMR-style pass so the final ``examples_top_k``
     aren't near-duplicates of each other (same tables AND same SQL shape).

Never raises: any failure at any stage falls back to a static head-of-list slice, the
same contract ``example_index.rank_examples`` has always had.
"""

from __future__ import annotations

from itertools import combinations

from sql_agent.config import settings
from sql_agent.logging_config import get_logger
from sql_agent.memory import example_index
from sql_agent.memory.column_selector import select_columns
# Metadata reading lives beside the index build now (one reader, no drift with the
# vector-store payloads) — kept under the old local name for the tests that patch it.
from sql_agent.memory.example_index import row_metadata as _row_metadata
from sql_agent.routing.intent_tagger import expected_patterns, tag_intent
from sql_agent.semantic_layer.glossary import matched_terms

log = get_logger("examples")


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _join_table_pairs(joins: list[str]) -> set[frozenset]:
    """``["t1.col = t2.col", ...]`` -> ``{frozenset({"t1", "t2"}), ...}`` — join
    OVERLAP is scored at the table-pair level (a live question has no column-level join
    expectation of its own, only which tables it touches)."""
    pairs: set[frozenset] = set()
    for j in joins:
        left, _, right = j.partition("=")
        lt = left.strip().split(".")[0]
        rt = right.strip().split(".")[0]
        if lt and rt:
            pairs.add(frozenset({lt, rt}))
    return pairs


def _example_similarity(meta_a: dict, meta_b: dict) -> float:
    """Similarity between two EXAMPLES (not question-vs-example) for the diversity
    pass: average of table overlap and SQL-pattern overlap — two examples that touch
    the same tables AND teach the same query shape are the near-duplicates diversity
    should avoid stacking in the same prompt."""
    t = _jaccard(set(meta_a.get("tables") or []), set(meta_b.get("tables") or []))
    p = _jaccard(set(meta_a.get("sql_pattern") or []), set(meta_b.get("sql_pattern") or []))
    return (t + p) / 2


def rank_examples(
    question: str,
    rows: list[dict],
    tier: str | None = None,
    tables_hint: list[str] | None = None,
    k: int | None = None,
) -> list[dict]:
    """Return the top-``k`` most relevant AND diverse example rows for ``question``.

    See the module docstring for the pipeline. Never raises: on any failure it returns
    a static head slice so generation is never starved of (or broken by) examples.
    """
    if not rows:
        return []
    k = k or settings.examples_top_k

    try:
        # --- Structured retrieval key: derive the question's signals FIRST ---------
        # (scale mode passes them INTO the fetch — payload filter + enriched query
        # text; with the flags off they only feed the post-search filter/score below.)
        live_tables = {t for t in (tables_hint or []) if t}
        live_columns = select_columns(question, tables=live_tables or None)
        live_intent = tag_intent(question)
        live_patterns = expected_patterns(question)

        # Everything the ranker derived from THIS question — logged so a live turn's
        # example picks can be audited without reproducing the request offline.
        log.info(
            "PATTERN signals | intent=%s | patterns=%s | glossary_terms=%s | "
            "tables_hint=%s | columns=%s",
            live_intent, sorted(live_patterns), matched_terms(question),
            sorted(live_tables), sorted(live_columns),
        )

        fused, dense, prefiltered = example_index.semantic_signal(
            question, rows,
            live_tables=live_tables, intent=live_intent, patterns=live_patterns,
        )
        if not fused:  # neither ranker available -> static head slice (today's floor)
            return rows[:k]

        # Confidence gate (unchanged contract): when a dense signal is available, drop
        # examples whose cosine similarity is below the floor; if NOTHING clears it,
        # inject no examples at all rather than a misleading one.
        min_score = settings.examples_min_score
        if dense and min_score > 0:
            fused = {i: sc for i, sc in fused.items() if dense.get(i, 0.0) >= min_score}
            if not fused:
                best = max(dense.values())
                log.info("PATTERN retrieve | %d examples | best score %.3f < floor %.2f "
                         "| no examples injected", len(rows), best, min_score)
                return []

        # --- Phase 4: metadata filter -------------------------------------------
        metas = {i: _row_metadata(rows[i]) for i in fused}
        if prefiltered:
            # The vector store already applied the table filter (payload where) —
            # everything in `fused` is table-eligible; re-filtering here is redundant.
            eligible = set(fused)
            log.info("PATTERN filter | corpus=%d | store-prefiltered=%d (tables=%s)",
                     len(rows), len(fused), sorted(live_tables))
        elif live_tables:
            filtered = {i for i in fused if set(metas[i].get("tables") or []) & live_tables}
            eligible = filtered or set(fused)  # never starve: empty intersection -> full pool
            log.info(
                "PATTERN filter | corpus=%d | passed_gate=%d | table_overlap=%d%s",
                len(rows), len(fused), len(filtered),
                "" if filtered else " | empty -> using full gated pool",
            )
        else:
            eligible = set(fused)
            log.info("PATTERN filter | corpus=%d | passed_gate=%d | no tables_hint "
                     "-> no metadata filter", len(rows), len(fused))

        # --- Phase 10: bounded candidate pool by the existing semantic ranking --
        pool_size = min(settings.examples_candidate_pool_k, len(eligible))
        pool = sorted(eligible, key=lambda i: fused[i], reverse=True)[:pool_size]

        # --- Phase 8: weighted multi-factor score -------------------------------
        pool_scores = [fused[i] for i in pool]
        lo, hi = min(pool_scores), max(pool_scores)
        spread = hi - lo

        w_sem = settings.examples_weight_semantic
        w_table = settings.examples_weight_table
        w_col = settings.examples_weight_column
        w_intent = settings.examples_weight_intent
        w_pattern = settings.examples_weight_pattern
        w_join = settings.examples_weight_join
        total_w = w_sem + w_table + w_col + w_intent + w_pattern + w_join or 1.0

        final: dict[int, float] = {}
        factors: dict[int, tuple] = {}  # per-candidate factor breakdown, for the pick log
        for i in pool:
            meta = metas[i]
            semantic_norm = (fused[i] - lo) / spread if spread > 0 else 1.0
            table_ov = _jaccard(set(meta.get("tables") or []), live_tables)
            column_ov = _jaccard(set(meta.get("columns") or []), live_columns)
            intent_match = 1.0 if meta.get("intent") == live_intent else 0.0
            pattern_ov = _jaccard(set(meta.get("sql_pattern") or []), live_patterns)
            join_ov = _jaccard(_join_table_pairs(meta.get("joins") or []),
                               {frozenset(p) for p in combinations(live_tables, 2)})
            score = (w_sem * semantic_norm + w_table * table_ov + w_col * column_ov
                     + w_intent * intent_match + w_pattern * pattern_ov + w_join * join_ov) / total_w
            if tier and rows[i].get("tier") == tier:
                score += 0.02  # minor legacy tier-match nudge; tables/pattern now carry the signal
            final[i] = score
            factors[i] = (semantic_norm, table_ov, column_ov, intent_match, pattern_ov, join_ov)

        # --- Phase 9: MMR-style diversity re-ranking ----------------------------
        lam = settings.examples_diversity_lambda
        remaining = sorted(pool, key=lambda i: final[i], reverse=True)
        selected: list[int] = []
        while remaining and len(selected) < k:
            if not selected:
                best = remaining[0]
            else:
                def mmr(i: int) -> float:
                    max_sim = max(_example_similarity(metas[i], metas[s]) for s in selected)
                    return lam * final[i] - (1 - lam) * max_sim

                best = max(remaining, key=mmr)
            selected.append(best)
            remaining.remove(best)

        top = [rows[i] for i in selected]
        log.info("PATTERN retrieve | %d examples | pool=%d | picked=%d",
                 len(rows), len(pool), len(top))
        # One line per pick with its full factor breakdown (same order as the weights:
        # semantic/table/column/intent/pattern/join) so "why THIS example?" is
        # answerable straight from the live logs.
        for rank, i in enumerate(selected, 1):
            sem, tab, col, intent_m, pat, join_ov = factors[i]
            log.info(
                "PATTERN pick %d | final=%.3f | sem=%.2f tab=%.2f col=%.2f "
                "int=%.0f pat=%.2f join=%.2f | tables=%s intent=%s | %s",
                rank, final[i], sem, tab, col, intent_m, pat, join_ov,
                sorted(metas[i].get("tables") or []), metas[i].get("intent", "?"),
                rows[i].get("question", "")[:60],
            )
        return top
    except Exception as exc:  # noqa: BLE001 — retrieval must never break the turn
        log.warning("example retrieval failed | %s | static head slice", exc)
        return rows[:k]
