"""Tool selection evaluator — did the agent pick the right MCP SQL-view tool?

Extends data_tool_evaluator with granular scoring:
  1.0 — correct tool called
  0.5 — a different known tool was called (wrong view, but tool call succeeded)
  0.0 — no tool was called at all
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

from evaluators.compliance_evaluator import EvalScore
from evaluators.data_tool_evaluator import (
    QUERY_TYPE_TO_TOOL,
    ALL_KNOWN_TOOLS,
)

_EVAL_ROOT = Path(__file__).resolve().parents[1]
if str(_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_ROOT))


def tool_selection_score(
    agent_outputs: List[str],
    query_type: str,
) -> EvalScore:
    """Score tool selection with 3-tier granularity.

    agent_outputs: list of string outputs / logs from the DataAgent hop.
    query_type: keyword matching QUERY_TYPE_TO_TOOL keys (e.g. "profitability", "margin").
    """
    expected_tool = QUERY_TYPE_TO_TOOL.get(query_type.lower())
    if not expected_tool:
        return EvalScore(1.0, "NOT_APPLICABLE", f"no expected tool for query_type={query_type!r}")

    combined = " ".join(agent_outputs).lower()

    if expected_tool.lower() in combined:
        return EvalScore(1.0, "CORRECT_TOOL", f"expected={expected_tool}")

    # Check if any other known tool was called (wrong view, partial credit)
    wrong_tools = [t for t in ALL_KNOWN_TOOLS if t.lower() != expected_tool.lower() and t.lower() in combined]
    if wrong_tools:
        return EvalScore(
            0.5,
            "WRONG_TOOL",
            f"expected={expected_tool}, got={wrong_tools[0]}",
        )

    return EvalScore(0.0, "NO_TOOL_CALLED", f"expected={expected_tool}")
