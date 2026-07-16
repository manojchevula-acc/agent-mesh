"""Tool input accuracy evaluator.

Verifies that tool call inputs were correct:
  - customer_id in query matches customer_id passed to the tool
  - no raw PII was passed as a tool argument
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

_CUST_RE = re.compile(r"\bCUST[_-]?\d{3,}\b", re.IGNORECASE)


def tool_input_accuracy_score(
    query: str,
    agent_outputs: List[str],
    audit_records: Optional[List[dict]] = None,
) -> EvalScore:
    """Score whether tool inputs correctly matched the query intent.

    Checks:
    1. customer_id in query appears in agent outputs / audit parameters
    2. no raw PII patterns in tool arguments (delegate to pii_evaluator)
    """
    query_customers = set(c.upper() for c in _CUST_RE.findall(query))

    if not query_customers:
        # No customer ID in query — check for PII in tool outputs only
        combined = " ".join(agent_outputs)
        pii_result = _check_pii_in_tool_args(combined)
        checks = [
            {"name": "Customer IDs in query", "passed": True,
             "detail": "No customer IDs in query — only checking PII in tool arguments"},
            {"name": "No PII detected in tool arguments",
             "passed": pii_result.score == 1.0,
             "detail": "Clean — no PII patterns" if pii_result.score == 1.0
                       else pii_result.detail or "PII detected in tool args"},
        ]
        return EvalScore(pii_result.score, pii_result.label, pii_result.detail, checks=checks)

    # Check that expected customer IDs appear in tool outputs / audit
    combined_output = " ".join(agent_outputs)
    combined_audit = " ".join(
        str(r.get("inputs", "")) + str(r.get("output", ""))
        for r in (audit_records or [])
    )
    combined = (combined_output + " " + combined_audit).upper()

    checks = []
    matched = []
    missing = []
    for c in sorted(query_customers):
        found = c in combined
        checks.append({
            "name": f"Customer ID {c} threaded into tool call",
            "passed": found,
            "detail": "Found in tool arguments / audit output" if found
                      else "Missing — ID from query not passed to tool",
        })
        if found:
            matched.append(c)
        else:
            missing.append(c)

    # PII check on tool args
    pii_result = _check_pii_in_tool_args(combined_output)
    checks.append({
        "name": "No PII detected in tool arguments",
        "passed": pii_result.score == 1.0,
        "detail": "Clean — no PII patterns in tool args" if pii_result.score == 1.0
                  else pii_result.detail or "PII detected in tool args",
    })

    if missing:
        return EvalScore(0.0, "WRONG_CUSTOMER_ID",
                         f"query had {query_customers}, missing in tool call: {missing}",
                         checks=checks)

    if pii_result.score < 1.0:
        return EvalScore(0.5, "PII_IN_TOOL_ARGS",
                         pii_result.detail or "raw PII detected in tool arguments",
                         checks=checks)

    return EvalScore(1.0, "INPUTS_CORRECT",
                     f"customer_ids matched: {matched}", checks=checks)


def _check_pii_in_tool_args(text: str) -> EvalScore:
    from evaluators.pii_evaluator import pii_not_in_response
    result = pii_not_in_response(text)
    return result
