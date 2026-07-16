"""Ambiguity resolution evaluator for FAB AgentMesh.

Tests whether the agent asks for clarification when a query is vague (missing
customer ID, timeframe, product, or entity) rather than silently assuming intent.

Scoring:
  1.0 — CLARIFICATION_REQUESTED: agent explicitly asked a follow-up question
  0.5 — INTENT_ASSUMED: agent answered with an assumption (may be acceptable)
  0.0 — HALLUCINATED: agent fabricated specifics not grounded in the query
"""
from __future__ import annotations

import re
from typing import List, Optional

from evaluators.compliance_evaluator import EvalScore

# Phrases that signal the agent is seeking clarification
_CLARIFICATION_PATTERNS: List[str] = [
    r"which customer",
    r"which (?:account|loan|facility|product|entity|counterparty)",
    r"could you (?:please )?(?:clarify|specify|provide|confirm)",
    r"can you (?:please )?(?:clarify|specify|provide|confirm)",
    r"please (?:clarify|specify|provide|confirm|let me know)",
    r"what (?:customer|account|period|timeframe|date range|product|entity) (?:are you|do you)",
    r"do you mean",
    r"could you tell me (?:which|what|who)",
    r"what do you mean by",
    r"to (?:help|assist) you (?:better|further),? (?:could|can|please)",
    r"i(?:'d| would) need (?:more|additional) (?:information|details|context)",
    r"(?:more|additional) (?:information|details|context) (?:is|would be) (?:needed|required|helpful)",
]

# Markers that suggest the agent hallucinated specifics
_HALLUCINATION_MARKERS: List[str] = [
    r"CUST\d{3}",          # fabricated customer IDs
    r"\b\d{1,3}\.\d{1,2}%",  # fabricated percentage figures
    r"as of \d{4}",           # fabricated date anchors
    r"(?:AED|USD|EUR)\s*[\d,]+",  # fabricated currency amounts
]


def ambiguity_resolution_score(
    query: str,
    response: str,
    expected_keywords: Optional[List[str]] = None,
) -> EvalScore:
    """Score whether the agent handled an ambiguous query appropriately.

    Args:
        query: The original user query (expected to be vague/underspecified).
        response: The agent's response.
        expected_keywords: If provided, presence of these confirms hallucination
                           when response doesn't ask for clarification.
    """
    if not response or not response.strip():
        return EvalScore(0.0, "EMPTY_RESPONSE", "No response to evaluate")

    response_lower = response.lower()

    # Check for clarification-seeking language
    for pattern in _CLARIFICATION_PATTERNS:
        if re.search(pattern, response_lower):
            return EvalScore(
                1.0,
                "CLARIFICATION_REQUESTED",
                f"Agent asked for clarification (matched: '{pattern}')",
            )

    # No clarification — check if the response hallucinated specifics
    hallucinated_fields = [
        m for m in _HALLUCINATION_MARKERS if re.search(m, response)
    ]
    if hallucinated_fields:
        return EvalScore(
            0.0,
            "HALLUCINATED",
            f"Agent fabricated specific details without clarifying: {hallucinated_fields[:3]}",
        )

    # Response answered but didn't hallucinate — partial credit (assumed intent)
    if expected_keywords:
        answer_lower = response.lower()
        hit_count = sum(1 for kw in expected_keywords if kw.lower() in answer_lower)
        if hit_count == 0:
            return EvalScore(
                0.0,
                "HALLUCINATED",
                "Response neither clarified nor addressed any expected keywords",
            )

    return EvalScore(
        0.5,
        "INTENT_ASSUMED",
        "Agent answered without asking for clarification — may have assumed intent correctly",
    )
