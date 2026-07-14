"""Intent resolution evaluator — did PriceAssistAgent route to the correct agents?

  intent=data      → DataAgent must have been called
  intent=knowledge → RAGAgent must have been called
  intent=hybrid    → both must have been called

Intent is inferred from route_type (from GoldenTestCase), which mirrors
fab.domain.intent span attribute set by the DomainExecutor in workflow.py.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

from evaluators.compliance_evaluator import EvalScore

_EVAL_ROOT = Path(__file__).resolve().parents[1]
if str(_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_ROOT))

# Maps route_type to expected agent(s)
_ROUTE_TO_AGENTS: dict[str, list[str]] = {
    "data": ["DataAgent"],
    "knowledge": ["RAGAgent"],
    "hybrid": ["DataAgent", "RAGAgent"],
}


def intent_resolution_score(
    route_type: str,
    audit_records: List[dict],
) -> EvalScore:
    """Score whether the correct downstream agent(s) were called for the intent.

    Returns NOT_APPLICABLE (1.0) for routes not in the intent map
    (blocked, multi_turn, replay, etc.).
    """
    expected_agents = _ROUTE_TO_AGENTS.get(route_type)
    if not expected_agents:
        return EvalScore(1.0, "NOT_APPLICABLE")

    called_agents = {r.get("agent_name", "") for r in audit_records}

    correct = [a for a in expected_agents if a in called_agents]
    missing = [a for a in expected_agents if a not in called_agents]

    if not missing:
        return EvalScore(1.0, "INTENT_RESOLVED", f"all expected agents called: {expected_agents}")
    elif correct:
        return EvalScore(
            0.5,
            "INTENT_PARTIAL",
            f"called={correct}, missing={missing}",
        )
    else:
        return EvalScore(
            0.0,
            "INTENT_WRONG",
            f"expected {expected_agents}, called agents: {list(called_agents)}",
        )
