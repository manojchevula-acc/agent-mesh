"""Task completion evaluator — deterministic field-presence checks.

Verifies that the task was actually completed, not just attempted.
  data    — response contains structured fields (name, % value, currency amount)
  knowledge — response contains a policy citation
  hybrid  — response contains BOTH structured fields AND a citation
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

from evaluators.compliance_evaluator import EvalScore

_EVAL_ROOT = Path(__file__).resolve().parents[1]
if str(_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_ROOT))

# Patterns that indicate structured data was returned
_PERCENT_RE = re.compile(r"\d+(\.\d+)?\s*%")
_CURRENCY_RE = re.compile(r"(AED|USD|EUR|GBP)\s*[\d,]+|[\d,]+\s*(AED|USD|EUR|GBP)", re.IGNORECASE)
_CUSTOMER_NAME_RE = re.compile(
    r"\b(acme|globex|initech|techcorp|omega|corp|ltd|llc|inc|company|customer)\b",
    re.IGNORECASE,
)


def task_completion_score(
    response: str,
    route_type: str,
) -> EvalScore:
    """Score task completion deterministically based on route type.

    route_type: "data" | "knowledge" | "hybrid"
    Any other route_type returns 1.0 (not applicable).
    """
    if not response or not response.strip():
        return EvalScore(0.0, "EMPTY_RESPONSE")

    if route_type == "data":
        return _check_data_completion(response)
    elif route_type == "knowledge":
        return _check_knowledge_completion(response)
    elif route_type == "hybrid":
        return _check_hybrid_completion(response)
    else:
        return EvalScore(1.0, "NOT_APPLICABLE")


def _check_data_completion(response: str) -> EvalScore:
    has_percent = bool(_PERCENT_RE.search(response))
    has_currency = bool(_CURRENCY_RE.search(response))
    has_name = bool(_CUSTOMER_NAME_RE.search(response))

    hits = sum([has_percent, has_currency, has_name])
    if hits >= 2:
        return EvalScore(1.0, "DATA_COMPLETE", f"fields found: percent={has_percent}, currency={has_currency}, name={has_name}")
    elif hits == 1:
        return EvalScore(0.5, "DATA_PARTIAL", f"only 1 of 3 expected data fields found")
    else:
        return EvalScore(0.0, "DATA_MISSING", "no structured data fields detected")


def _check_knowledge_completion(response: str) -> EvalScore:
    from evaluators.rag_citation_evaluator import citation_present_and_valid
    cit = citation_present_and_valid(response)
    if cit.score >= 1.0:
        return EvalScore(1.0, "KNOWLEDGE_COMPLETE")
    elif cit.score >= 0.5:
        return EvalScore(0.5, "KNOWLEDGE_WEAK_CITATION")
    else:
        return EvalScore(0.0, "KNOWLEDGE_NO_CITATION")


def _check_hybrid_completion(response: str) -> EvalScore:
    data_result = _check_data_completion(response)
    knowledge_result = _check_knowledge_completion(response)
    combined = (data_result.score + knowledge_result.score) / 2.0
    if combined >= 0.9:
        return EvalScore(1.0, "HYBRID_COMPLETE")
    elif combined >= 0.4:
        return EvalScore(0.5, "HYBRID_PARTIAL", f"data={data_result.score}, citation={knowledge_result.score}")
    else:
        return EvalScore(0.0, "HYBRID_MISSING", f"data={data_result.score}, citation={knowledge_result.score}")
