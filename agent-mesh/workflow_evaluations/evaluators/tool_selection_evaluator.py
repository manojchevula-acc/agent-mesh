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
    tool_names_from_reasoning: Optional[List[str]] = None,
) -> EvalScore:
    """Score tool selection with 3-tier granularity.

    agent_outputs: list of string outputs / logs from the DataAgent hop.
    query_type: keyword matching QUERY_TYPE_TO_TOOL keys (e.g. "profitability", "margin").
    tool_names_from_reasoning: tool names extracted from <llm_reasoning> blocks before
        stripping. Preferred over free-text search since the tool name appears only in
        the reasoning block, which is stripped before outputs reach this function.
    """
    expected_tool = QUERY_TYPE_TO_TOOL.get(query_type.lower())
    if not expected_tool:
        return EvalScore(1.0, "NOT_APPLICABLE",
                         f"no expected tool for query_type={query_type!r}",
                         checks=[
                             {"name": f"Expected tool for query type '{query_type}'",
                              "passed": True,
                              "detail": "No expected tool in mapping — evaluation not applicable"},
                         ])

    combined = " ".join(agent_outputs).lower()
    reasoning_tools_lower = [t.lower() for t in (tool_names_from_reasoning or [])]

    # Prefer reasoning-extracted tool names (reliable); fall back to free-text search
    if reasoning_tools_lower:
        tool_found = expected_tool.lower() in reasoning_tools_lower
        wrong_tools = [t for t in reasoning_tools_lower
                       if t != expected_tool.lower() and t in {x.lower() for x in ALL_KNOWN_TOOLS}]
    else:
        tool_found = expected_tool.lower() in combined
        wrong_tools = [t for t in ALL_KNOWN_TOOLS
                       if t.lower() != expected_tool.lower() and t.lower() in combined]

    checks = [
        {"name": f"Expected tool identified for query keyword '{query_type}'",
         "passed": True,
         "detail": f"Expected tool: {expected_tool}"},
        {"name": f"Expected tool '{expected_tool}' found in DataAgent output",
         "passed": tool_found,
         "detail": "Tool call detected in agent output" if tool_found
                   else "Tool not found in DataAgent output"},
        {"name": "No alternative (wrong) tool called instead",
         "passed": not wrong_tools,
         "detail": "No unexpected tool calls" if not wrong_tools
                   else f"Wrong tool(s) found: {', '.join(wrong_tools)}"},
    ]

    if tool_found:
        return EvalScore(1.0, "CORRECT_TOOL", f"expected={expected_tool}", checks=checks)
    if wrong_tools:
        return EvalScore(0.5, "WRONG_TOOL",
                         f"expected={expected_tool}, got={wrong_tools[0]}", checks=checks)
    return EvalScore(0.0, "NO_TOOL_CALLED", f"expected={expected_tool}", checks=checks)
