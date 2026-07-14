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
        return pii_result

    # Check that at least one expected customer ID appears in tool outputs
    combined_output = " ".join(agent_outputs)
    combined_audit = " ".join(
        str(r.get("inputs", "")) + str(r.get("output", ""))
        for r in (audit_records or [])
    )
    combined = (combined_output + " " + combined_audit).upper()

    matched = [c for c in query_customers if c in combined]
    missing = [c for c in query_customers if c not in combined]

    if missing:
        return EvalScore(
            0.0,
            "WRONG_CUSTOMER_ID",
            f"query had {query_customers}, missing in tool call: {missing}",
        )

    # Also check for PII leakage in tool args
    pii_result = _check_pii_in_tool_args(combined_output)
    if pii_result.score < 1.0:
        return EvalScore(
            0.5,
            "PII_IN_TOOL_ARGS",
            pii_result.detail or "raw PII detected in tool arguments",
        )

    return EvalScore(1.0, "INPUTS_CORRECT", f"customer_ids matched: {matched}")


def _check_pii_in_tool_args(text: str) -> EvalScore:
    from evaluators.pii_evaluator import pii_not_in_response
    result = pii_not_in_response(text)
    return result
