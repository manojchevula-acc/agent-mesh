"""Deterministic (non-LLM) evaluation layer for the text-to-SQL agent.

This package is the SECOND opinion. The existing evaluation asks an LLM to judge whether
the agent's SQL is equivalent to gold and whether the rows match (eval/compare_llm.py's
`judge`). That verdict is expensive, occasionally flaky, and impossible to audit — you
cannot re-derive *why* it said EQUIVALENT. This layer answers the same questions with
transparent, reproducible arithmetic instead: fuzzy string matching, structural SQL
comparison (sqlglot), schema-aware entity matching, and result-set similarity metrics.

Neither verdict is "the truth". The point is to run BOTH and study where they diverge —
a deterministic PASS on an LLM FAIL usually means the LLM was fooled by cosmetics; an LLM
PASS on a deterministic FAIL usually means a semantic equivalence the metrics cannot see
(and is a candidate for a new deterministic rule). eval/deterministic/agreement.py
quantifies that divergence (Cohen's Kappa, stricter/leaner counts).

Layout
------
    text_sim.py        edit distance, normalized ratio, Jaccard — pure Python, no deps
    sql_structure.py   parse SQL -> footprint; precision/recall/F1 per schema element
    result_metrics.py  row & cell precision/recall/F1, exact match, Jaccard, fuzzy
    schema_semantic.py ID <-> name equivalence via lookup tables (product/customer/...)
    evaluator.py       compose all of the above -> one DeterministicResult + a registry
    agreement.py       LLM-vs-deterministic agreement, Cohen's Kappa

Nothing here imports an LLM. The only optional external touch is schema_semantic.py, which
may read lookup tables from the live DB to map IDs to names — and degrades gracefully to a
"cannot decide" when the DB is absent, never to a wrong verdict.
"""

from __future__ import annotations

from eval.deterministic.evaluator import (
    DeterministicEvaluator,
    DeterministicResult,
    evaluate,
)

__all__ = ["DeterministicEvaluator", "DeterministicResult", "evaluate"]
