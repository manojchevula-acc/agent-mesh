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

# Group markers by category for per-check reporting
_ERROR_CATEGORIES: List[tuple] = [
    ("No MCP tool errors (MCP_TOOL_ERROR / mcp_error)", ["MCP_TOOL_ERROR", "mcp_error"]),
    ("No timeout errors (A2A_TIMEOUT / timeout)", ["A2A_TIMEOUT", "timeout"]),
    ("No SQL view errors (SQL_VIEW_NOT_FOUND)", ["SQL_VIEW_NOT_FOUND"]),
    ("No tool execution errors (tool_error)", ["tool_error"]),
    ("No connection errors (connection_error)", ["connection_error"]),
]


def tool_call_success_score(
    audit_records: List[dict],
    agent_outputs: Optional[List[str]] = None,
) -> EvalScore:
    """Score whether all tool calls completed without errors.

    Checks audit record status fields and known error string markers.
    Returns NOT_APPLICABLE if no DataAgent records are present.
    """
    data_records = [
        r for r in audit_records
        if r.get("agent_name") in ("DataAgent", "RAGAgent")
    ]

    if not data_records and not (agent_outputs or []):
        return EvalScore(1.0, "NOT_APPLICABLE", "no tool-calling agent records found", checks=[
            {"name": "DataAgent / RAGAgent records present in audit trail",
             "passed": False,
             "detail": "No tool-calling agent records — evaluation not applicable"},
        ])

    # Collect all error markers found
    errors_found: List[str] = []
    status_error = False

    for rec in data_records:
        status = str(rec.get("status", "")).lower()
        if status in ("error", "failed", "timeout"):
            errors_found.append(f"audit_status={status} agent={rec.get('agent_name')}")
            status_error = True
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

    # Build per-category checks
    all_error_text = " ".join(errors_found).upper()
    checks: List[dict] = [
        {"name": "DataAgent / RAGAgent records present in audit trail",
         "passed": bool(data_records),
         "detail": f"{len(data_records)} record(s) found"},
    ]
    for label, markers in _ERROR_CATEGORIES:
        fired = any(m.upper() in all_error_text for m in markers)
        checks.append({
            "name": label,
            "passed": not fired,
            "detail": "Clean" if not fired
                      else f"Error detected: {next((m for m in markers if m.upper() in all_error_text), markers[0])}",
        })
    if status_error:
        # If status was error/failed/timeout, mark audit status check
        for chk in checks:
            if "status" in chk["name"].lower():
                chk["passed"] = False

    if errors_found:
        return EvalScore(0.0, "TOOL_ERROR", "; ".join(errors_found[:3]), checks=checks)

    return EvalScore(1.0, "TOOL_SUCCESS", checks=checks)
