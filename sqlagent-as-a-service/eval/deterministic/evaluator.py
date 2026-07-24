"""Compose the deterministic signals into ONE verdict — the LLM judge's counterpart.

eval/compare_llm.judge asks a model for a PASS/FAIL-shaped opinion. This is the
deterministic answer to the same question, built only from the metrics in this package:

    result_metrics   did the ROWS match? (the ground truth for correctness)
    schema_semantic  if not, are they the SAME ENTITIES under a swapped id/name key?
    sql_structure    how similarly were the two queries CONSTRUCTED? (diagnostic, not a gate)

VERDICT LOGIC (deliberately mirrors compare_llm's "data decides, structure explains")
------------------------------------------------------------------------------------
A result is PASS if the rows match exactly, OR the schema-aware matcher proves the two
sets name the same entities. Structural similarity NEVER flips the verdict — a different
but correct query must pass, and a copy of gold's SQL that returned nothing must fail — it
only feeds `confidence` and the diagnosis. This is the same principle the existing
deterministic layer already enforces; here it is made explicit and paired with a
confidence score so the LLM-vs-deterministic comparison has something graded to weigh.

EXTENSIBILITY
-------------
Every scalar the dashboard tracks is produced by a named entry in METRIC_REGISTRY. Adding
a new deterministic metric (say, a column-name similarity, or a query-cost estimate) is
registering one function `(item, run) -> float|None` — it then appears in every result's
`metrics` map and can be aggregated by the dashboard with no other change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from eval.deterministic import result_metrics, sql_structure
from eval.deterministic.schema_semantic import SchemaSemanticMatcher


@dataclass
class DeterministicResult:
    verdict: str                        # "PASS" | "FAIL" — STRICT: execution correctness,
                                         # full gold_columns required. The source of truth.
    confidence: float                   # [0,1] — how sure the arithmetic is
    diagnosis: str                      # single most-informative outcome label
    evaluable: bool                     # did the agent return rows to grade at all?
    exact_sql_match: bool
    structural_similarity: float
    semantic_equivalent: bool           # rows matched only via id<->name resolution
    core_answer_match: bool = False     # LENIENT, ADDITIVE second lens — see docstring below
    metrics: dict = field(default_factory=dict)     # flat scalar map for the dashboard
    sql_elements: dict = field(default_factory=dict)  # per-element P/R/F1 detail
    result_detail: dict = field(default_factory=dict)
    semantic_reason: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict == "PASS"


class DeterministicEvaluator:
    """Runs the deterministic layer over (gold item, agent run) pairs.

    Construct once per eval run so the schema-aware matcher's lookup cache (and its single
    DB touch per entity) is shared across every item.
    """

    def __init__(self, numeric_tolerance: float = 0.01, matcher: SchemaSemanticMatcher | None = None):
        self.numeric_tolerance = numeric_tolerance
        self.matcher = matcher or SchemaSemanticMatcher()

    def evaluate(self, item: dict, run: dict) -> DeterministicResult:
        """item: a gold_dynamic entry (question, gold_sql, gold_result, order_sensitive...).
        run:  an agent_runs entry (agent_sql, agent_result, status...)."""
        gold_sql = item.get("gold_sql") or ""
        agent_sql = run.get("agent_sql") or ""
        gold_rows = item.get("gold_result")
        agent_rows = run.get("agent_result")
        # Order matters only when the DATASET marks it AND the SQL is structurally a
        # ranking (ORDER BY + a deliberately-chosen LIMIT below the system's blanket row
        # cap — see sql_structure.is_order_significant). ANDing the two means this can only
        # ever RELAX an item, never tighten one the dataset didn't already flag: a plain
        # listing whose ORDER BY exists only for gold's own reproducibility (`ORDER BY
        # deal_id`, with no LIMIT or with the system's forced LIMIT 50) is compared as an
        # unordered set, so a correct agent is not failed for a harmless permutation.
        order_sensitive = (bool(item.get("order_sensitive", False))
                           and sql_structure.is_order_significant(gold_sql))
        tol = item.get("numeric_tolerance", self.numeric_tolerance)

        struct = sql_structure.compare_footprints(gold_sql, agent_sql)
        res = result_metrics.compare_results(
            gold_rows, agent_rows, order_sensitive=order_sensitive,
            numeric_tolerance=tol, fuzzy=True)

        # Schema-aware second chance: only consulted when rows did NOT already match, so a
        # straightforward match is never second-guessed and the DB is spared the lookup.
        sem = (self.matcher.equivalent(gold_rows, agent_rows, numeric_tolerance=tol)
               if not res.exact_match else None)
        semantic_equiv = bool(sem and sem.decidable and sem.equivalent)

        # "Evaluable" = the agent actually returned rows to grade. A rate-limited / errored
        # / no-SQL run produced NOTHING, so its zero scores are the absence of an answer,
        # not a measured wrong answer — the dashboard reports these apart so a handful of
        # rate-limits cannot masquerade as poor SQL quality across every metric.
        evaluable = agent_rows is not None

        passed = res.exact_match or semantic_equiv
        verdict = "PASS" if passed else "FAIL"
        confidence = self._confidence(res, struct, semantic_equiv, sem, evaluable)

        # Extra signals that let the diagnosis name a NEAR-miss precisely instead of the
        # catch-all "semantically-wrong-sql". These do NOT change the verdict — the dataset
        # marks a ranking order-sensitive on purpose, and an omitted column is an
        # incomplete answer — but they tell the reader the values were actually right.
        order_only = (evaluable and not res.exact_match and res.jaccard == 1.0
                      and res.gold_rows == res.agent_rows and order_sensitive)
        missing_cols = self._subset_values_match(gold_rows, agent_rows, tol)
        diagnosis = self._diagnose(item, run, res, struct, semantic_equiv, sem,
                                   order_only, missing_cols)

        # core_answer_match — a SECOND, explicitly lenient verdict, additive only: does NOT
        # change `verdict`/`passed`. True when either the strict verdict already passed, OR
        # the agent's columns are a proper SUBSET of gold's and every value it DID return is
        # correct (see _subset_values_match). This deliberately does NOT try to guess
        # "was this column relevant to the question" from the question text — that is a
        # judgment call with no reliable structural signal (a dropped column can be either
        # decorative context or the actual metric being asked about), and this evaluator
        # stays fully deterministic rather than approximating it with a text heuristic.
        # Reported as its own metric/column so BOTH numbers are visible side by side; the
        # strict verdict remains the source of truth for pass/fail.
        core_answer_match = passed or missing_cols

        metrics = {name: fn(item, run, res, struct, sem)
                   for name, fn in METRIC_REGISTRY.items()}
        metrics["deterministic_pass"] = 1.0 if passed else 0.0

        return DeterministicResult(
            verdict=verdict, confidence=round(confidence, 3), diagnosis=diagnosis,
            evaluable=evaluable, exact_sql_match=struct["exact_match"],
            structural_similarity=struct["structural_similarity"],
            semantic_equivalent=semantic_equiv, core_answer_match=core_answer_match,
            semantic_reason=(sem.reason if sem else ""),
            metrics=metrics, sql_elements=struct["elements"],
            result_detail=res.as_dict())

    # ------------------------------------------------------------------ scoring
    @staticmethod
    def _confidence(res, struct, semantic_equiv: bool, sem, evaluable: bool) -> float:
        """How sure is the arithmetic in its PASS/FAIL?

        High when the row evidence is unambiguous (a clean exact match, or a large fuzzy
        gap on a fail). Lower in the grey zone — rows that are close but not exact, where a
        rounding/typo call could go either way — which is exactly where we EXPECT the LLM
        and the metrics to disagree, so the number should say 'look here'."""
        if not evaluable:
            # No answer was produced. This is not a *confident wrong answer*; there is
            # simply nothing to grade, so confidence in a SQL judgement is zero.
            return 0.0
        if res.exact_match:
            return 1.0
        if semantic_equiv:
            return sem.confidence
        # A fail: confident when the results are far apart, unsure when they nearly matched.
        gap = 1.0 - max(res.fuzzy_similarity, res.jaccard)
        base = 0.5 + 0.5 * gap
        # If the SQL is structurally near-identical yet rows differ, something subtle is off
        # — lower confidence so the disagreement analysis flags it for review.
        if struct["structural_similarity"] > 0.9:
            base = min(base, 0.7)
        return max(0.5, min(1.0, base))

    @staticmethod
    def _subset_values_match(gold_rows, agent_rows, tol) -> bool:
        """True when the agent returned a STRICT SUBSET of gold's columns and, on those
        shared columns, every value matches. Distinguishes "dropped a column gold showed"
        (an incomplete but otherwise-correct answer) from "returned wrong numbers"."""
        if not gold_rows or not agent_rows:
            return False
        gcols = {c.lower(): c for c in gold_rows[0]}
        acols = {c.lower(): c for c in agent_rows[0]}
        if not set(acols) < set(gcols):       # must be a proper subset by name
            return False
        g_sub = [{acols[k]: r[gcols[k]] for k in acols} for r in gold_rows]
        return result_metrics.compare_results(
            g_sub, agent_rows, order_sensitive=False, numeric_tolerance=tol).exact_match

    @staticmethod
    def _diagnose(item, run, res, struct, semantic_equiv, sem,
                  order_only=False, missing_cols=False) -> str:
        """One label, ordered by what actually happened — the deterministic counterpart to
        compare_llm.diagnose, but able to name schema-aware equivalence and near-misses."""
        status = run.get("status")
        if status in ("rate-limited", "agent-error", "no-tool", "no-sql", "sql-error"):
            return {"rate-limited": "rate-limited", "agent-error": "agent-error",
                    "no-tool": "no-tool-called", "no-sql": "no-sql-produced",
                    "sql-error": "sql-execution-failed"}[status]
        if res.exact_match:
            if struct["exact_match"]:
                return "correct-identical-sql"
            return "correct-equivalent-sql"
        if semantic_equiv:
            return "correct-semantic-equivalent"       # same entities, different key
        # Near-misses: values were right, but something the dataset counts still differs.
        if order_only:
            return "correct-rows-wrong-order"          # same rows, order-sensitive question
        if missing_cols:
            return "correct-values-missing-columns"    # agent omitted a gold column
        if sem and sem.decidable and not sem.equivalent:
            return "wrong-entities"
        tables = struct["elements"]["tables"]
        if tables["recall"] < 1.0:
            return "wrong-tables"
        if res.row_recall > 0 or res.fuzzy_similarity > 0.6:
            return "partially-correct-rows"
        return "semantically-wrong-sql"


# --------------------------------------------------------------------------- #
# Metric registry — the dashboard aggregates exactly what is registered here.
# Each function is (item, run, result_cmp, struct, semantic|None) -> float | None.
# None means "not applicable to this row" and is skipped in the mean.
# --------------------------------------------------------------------------- #
def _m_sql_exact(i, r, res, s, sem):
    return 1.0 if s["exact_match"] else 0.0


def _m_structural(i, r, res, s, sem):
    return s["structural_similarity"]


def _m_table_f1(i, r, res, s, sem):
    return s["elements"]["tables"]["f1"]


def _m_table_precision(i, r, res, s, sem):
    return s["elements"]["tables"]["precision"]


def _m_table_recall(i, r, res, s, sem):
    return s["elements"]["tables"]["recall"]


def _m_column_f1(i, r, res, s, sem):
    return s["elements"]["columns"]["f1"]


def _m_column_precision(i, r, res, s, sem):
    return s["elements"]["columns"]["precision"]


def _m_column_recall(i, r, res, s, sem):
    return s["elements"]["columns"]["recall"]


def _m_join_f1(i, r, res, s, sem):
    return s["elements"]["joins"]["f1"]


def _m_filter_f1(i, r, res, s, sem):
    return s["elements"]["filters"]["f1"]


def _m_groupby_f1(i, r, res, s, sem):
    return s["elements"]["group_by"]["f1"]


def _m_orderby_f1(i, r, res, s, sem):
    return s["elements"]["order_by"]["f1"]


def _m_agg_f1(i, r, res, s, sem):
    return s["elements"]["aggregations"]["f1"]


def _m_row_precision(i, r, res, s, sem):
    return res.row_precision


def _m_row_recall(i, r, res, s, sem):
    return res.row_recall


def _m_row_f1(i, r, res, s, sem):
    return res.row_f1


def _m_cell_accuracy(i, r, res, s, sem):
    return res.cell_accuracy


def _m_result_exact(i, r, res, s, sem):
    return 1.0 if res.exact_match else 0.0


def _m_jaccard(i, r, res, s, sem):
    return res.jaccard


def _m_fuzzy(i, r, res, s, sem):
    return res.fuzzy_similarity


def _m_semantic(i, r, res, s, sem):
    """1.0 if rows matched outright or via id<->name resolution; 0.0 if they genuinely
    differ; None when the schema-aware matcher could not decide (no lookup / no DB)."""
    if res.exact_match:
        return 1.0
    if sem is None or not sem.decidable:
        return None
    return 1.0 if sem.equivalent else 0.0


def _m_core_answer(i, r, res, s, sem):
    """LENIENT, ADDITIVE second lens — 1.0 if the strict verdict already passed, OR the
    agent's result is a value-correct SUBSET of gold's columns (every column it DID return
    is right; it just returned fewer than gold). Never used to decide `verdict`/`passed` —
    that stays strict (execution correctness against the FULL gold_columns). This exists
    because "was a dropped column actually asked for by the question" has no reliable
    structural signal (it can be decorative context OR the metric being asked about), so
    rather than guess with a text heuristic this reports both numbers side by side and lets
    a human decide which one is the relevant bar for a given question."""
    if res.exact_match or (sem and sem.decidable and sem.equivalent):
        return 1.0
    tol = i.get("numeric_tolerance", 0.01)
    ok = DeterministicEvaluator._subset_values_match(
        i.get("gold_result"), r.get("agent_result"), tol)
    return 1.0 if ok else 0.0


METRIC_REGISTRY: dict[str, Callable] = {
    "sql_exact_match": _m_sql_exact,
    "sql_structural_similarity": _m_structural,
    "table_precision": _m_table_precision,
    "table_recall": _m_table_recall,
    "table_f1": _m_table_f1,
    "column_precision": _m_column_precision,
    "column_recall": _m_column_recall,
    "column_f1": _m_column_f1,
    "join_f1": _m_join_f1,
    "filter_f1": _m_filter_f1,
    "groupby_f1": _m_groupby_f1,
    "orderby_f1": _m_orderby_f1,
    "aggregation_f1": _m_agg_f1,
    "result_row_precision": _m_row_precision,
    "result_row_recall": _m_row_recall,
    "result_row_f1": _m_row_f1,
    "cell_accuracy": _m_cell_accuracy,
    "result_exact_match": _m_result_exact,
    "result_jaccard": _m_jaccard,
    "fuzzy_similarity": _m_fuzzy,
    "semantic_equivalence": _m_semantic,
    "core_answer_match": _m_core_answer,
}


# Module-level convenience so callers can do `from eval.deterministic import evaluate`.
_DEFAULT_EVALUATOR: DeterministicEvaluator | None = None


def evaluate(item: dict, run: dict, numeric_tolerance: float = 0.01) -> DeterministicResult:
    """One-shot evaluation with a shared default evaluator (keeps the lookup cache warm
    across calls). Use DeterministicEvaluator directly when you need a custom tolerance or
    an injected matcher."""
    global _DEFAULT_EVALUATOR
    if _DEFAULT_EVALUATOR is None or _DEFAULT_EVALUATOR.numeric_tolerance != numeric_tolerance:
        _DEFAULT_EVALUATOR = DeterministicEvaluator(numeric_tolerance=numeric_tolerance)
    return _DEFAULT_EVALUATOR.evaluate(item, run)
