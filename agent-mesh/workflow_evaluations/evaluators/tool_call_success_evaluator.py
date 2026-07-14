"""Tool call success evaluator.

Did tool calls complete without errors?
Checks audit records for error events: MCP_TOOL_ERROR, A2A_TIMEOUT, SQL_VIEW_NOT_FOUND.
Score 0.0 on any error, 1.0 on clean completion.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

from evaluators.compliance_evaluator import EvalScore

_EVAL_ROOT = Path(__file__).resolve().parents[1]
if str(_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_ROOT))

_ERROR_MARKERS = (
    "MCP_TOOL_ERROR",
    "A2A_TIMEOUT",
    "SQL_VIEW_NOT_FOUND",
    "tool_error",
    "timeout",
    "connection_error",
    "mcp_error",
)


def tool_call_success_score(
    audit_records: List[dict],
    agent_outputs: Optional[List[str]] = None,
) -> EvalScore:
    """Score whether all tool calls completed without errors.

    Checks audit record status fields and known error string markers.
    Returns NOT_APPLICABLE if no DataAgent records are present.
    """
    from typing import Optional

    data_records = [
        r for r in audit_records
        if r.get("agent_name") in ("DataAgent", "RAGAgent")
    ]

    if not data_records and not (agent_outputs or []):
        return EvalScore(1.0, "NOT_APPLICABLE", "no tool-calling agent records found")

    errors_found = []

    for rec in data_records:
        status = str(rec.get("status", "")).lower()
        if status in ("error", "failed", "timeout"):
            errors_found.append(f"audit_status={status} agent={rec.get('agent_name')}")

        output_str = str(rec.get("output", "")).upper()
        for marker in _ERROR_MARKERS:
            if marker.upper() in output_str:
                errors_found.append(f"{marker} in {rec.get('agent_name')} output")
                break

    for out in (agent_outputs or []):
        for marker in _ERROR_MARKERS:
            if marker.upper() in out.upper():
                errors_found.append(f"{marker} in agent output")
                break

    if errors_found:
        return EvalScore(
            0.0,
            "TOOL_ERROR",
            "; ".join(errors_found[:3]),
        )

    return EvalScore(1.0, "TOOL_SUCCESS")
