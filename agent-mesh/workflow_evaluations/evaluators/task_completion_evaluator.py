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
from typing import List, Optional

from evaluators.compliance_evaluator import EvalScore

_EVAL_ROOT = Path(__file__).resolve().parents[1]
if str(_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_ROOT))

# Patterns that indicate structured data was returned
_PERCENT_RE = re.compile(r"\d+(\.\d+)?\s*%")
_CURRENCY_RE = re.compile(
    r"(AED|USD|EUR|GBP|CAD|CHF|CNY|JPY|SGD)\s*[\d,]+"      # AED 50,000 or AED50,000
    r"|[\d,]+\s*(AED|USD|EUR|GBP|CAD|CHF|CNY|JPY|SGD)"      # 50,000 AED
    r"|\(?(AED|USD|EUR|GBP)\)?[^0-9\n]{0,30}[\d,]+",        # (AED) | 18,000,000 table column
    re.IGNORECASE,
)
# Structured-data presence signals.
# Previously this was a company-name regex containing "customer", "corp", "company",
# "ltd", "inc" — tokens that appear in virtually every banking response, making the
# check meaningless (always True).  The replacement checks for concrete structural
# evidence that the agent returned a data payload:
#   • a markdown table row  (|field|value|)
#   • a field:value line    (e.g. "**Customer Name** | Al Noor Trading")
#   • a customer-ID reference (CUST001, CUST_002, …)
_MD_TABLE_RE = re.compile(r"\|[^|\n]+\|[^|\n]+\|", re.MULTILINE)
_FIELD_VALUE_RE = re.compile(
    r"^\s*\*?\*?[A-Za-z][A-Za-z_ ]{2,30}\*?\*?\s*[|:]\s*\S",
    re.MULTILINE,
)
_CUST_ID_RE = re.compile(r"\bCUST[_-]?\d{3,}\b", re.IGNORECASE)


def task_completion_score(
    response: str,
    route_type: str,
) -> EvalScore:
    """Score task completion deterministically based on route type.

    route_type: "data" | "knowledge" | "hybrid"
    Any other route_type returns 1.0 (not applicable).
    """
    if not response or not response.strip():
        return EvalScore(0.0, "EMPTY_RESPONSE", checks=[
            {"name": "Response is non-empty", "passed": False, "detail": "Empty response — task not completed"}
        ])

    if route_type == "data":
        return _check_data_completion(response)
    elif route_type == "knowledge":
        return _check_knowledge_completion(response)
    elif route_type == "hybrid":
        return _check_hybrid_completion(response)
    else:
        return EvalScore(1.0, "NOT_APPLICABLE", checks=[
            {"name": f"Route type '{route_type}' — task completion check not applicable", "passed": True,
             "detail": "Blocked or unclassified routes are excluded from task completion scoring"}
        ])


def _check_data_completion(response: str) -> EvalScore:
    has_percent = bool(_PERCENT_RE.search(response))
    has_currency = bool(_CURRENCY_RE.search(response))
    # Structural data signals: markdown table, field:value pairs, or a customer ID.
    # These are far more specific than generic company-name tokens ("corp", "customer")
    # which appear in every banking response regardless of whether data was returned.
    has_structure = (
        bool(_MD_TABLE_RE.search(response))
        or bool(_FIELD_VALUE_RE.search(response))
        or bool(_CUST_ID_RE.search(response))
    )

    checks: List[dict] = [
        {"name": "Percentage / ratio value present (e.g. 12.5%)",
         "passed": has_percent,
         "detail": "Found" if has_percent else "Not found — expected a numeric % value"},
        {"name": "Currency amount present (AED / USD / EUR / GBP / …)",
         "passed": has_currency,
         "detail": "Found" if has_currency else "Not found — expected a monetary value"},
        {"name": "Structured data present (table, field:value rows, or customer ID)",
         "passed": has_structure,
         "detail": "Found" if has_structure else "Not found — no markdown table, field:value, or CUST### ID"},
    ]

    hits = sum([has_percent, has_currency, has_structure])
    if hits >= 2:
        return EvalScore(1.0, "DATA_COMPLETE",
                         f"signals found: percent={has_percent}, currency={has_currency}, structure={has_structure}",
                         checks=checks)
    elif hits == 1:
        return EvalScore(0.5, "DATA_PARTIAL", "only 1 of 3 expected data signals found", checks=checks)
    else:
        return EvalScore(0.0, "DATA_MISSING", "no structured data signals detected", checks=checks)


def _check_knowledge_completion(response: str) -> EvalScore:
    from evaluators.rag_citation_evaluator import citation_present_and_valid
    cit = citation_present_and_valid(response)
    if cit.score >= 1.0:
        return EvalScore(1.0, "KNOWLEDGE_COMPLETE", checks=cit.checks)
    elif cit.score >= 0.5:
        return EvalScore(0.5, "KNOWLEDGE_WEAK_CITATION", checks=cit.checks)
    else:
        return EvalScore(0.0, "KNOWLEDGE_NO_CITATION", checks=cit.checks)


def _check_hybrid_completion(response: str) -> EvalScore:
    data_result = _check_data_completion(response)
    knowledge_result = _check_knowledge_completion(response)
    combined = (data_result.score + knowledge_result.score) / 2.0
    combined_checks = (data_result.checks or []) + (knowledge_result.checks or [])
    if combined >= 0.9:
        return EvalScore(1.0, "HYBRID_COMPLETE", checks=combined_checks)
    elif combined >= 0.4:
        return EvalScore(0.5, "HYBRID_PARTIAL",
                         f"data={data_result.score}, citation={knowledge_result.score}",
                         checks=combined_checks)
    else:
        return EvalScore(0.0, "HYBRID_MISSING",
                         f"data={data_result.score}, citation={knowledge_result.score}",
                         checks=combined_checks)
