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
    r"which (?:account|loan|facility|product|entity|counterparty|deal|report|policy)",
    r"could you (?:please )?(?:clarify|specify|provide|confirm|share|tell me)",
    r"can you (?:please )?(?:clarify|specify|provide|confirm|share|tell me)",
    r"please (?:clarify|specify|provide|confirm|let me know|share|indicate)",
    r"what (?:customer|account|period|timeframe|date range|product|entity|deal|report) (?:are you|do you)",
    r"do you mean",
    r"could you tell me (?:which|what|who)",
    r"what do you mean by",
    r"to (?:help|assist) you (?:better|further),? (?:could|can|please)",
    r"i(?:'d| would) need (?:more|additional) (?:information|details|context|detail)",
    r"(?:more|additional) (?:information|details|context|detail) (?:is|would be) (?:needed|required|helpful)",
    r"before (?:i can|we can|proceeding),?\s+(?:i |please )?(?:need|require|would need)",
    r"(?:unable|not able) to (?:determine|answer|help|assist|check|retrieve|proceed) without",
    r"(?:please |kindly )?(?:clarify|confirm|specify|provide|share|indicate) (?:the|your|which)",
    r"(?:could|can) you (?:be more specific|elaborate|tell me more)",
    r"i(?:'d| would) (?:be happy|like) to help.{0,30}(?:which|what|who|please)",
]

# Markers that suggest the agent hallucinated specifics rather than asking for
# clarification.  IMPORTANT: these markers are ONLY applied when the response
# does NOT contain any clarification-seeking language.  When an agent correctly
# asks for clarification and includes a policy example or range for context
# (e.g. "pricing floors are typically 4–5%"), that is not hallucination —
# firing the markers in that case produces false HALLUCINATED verdicts.
_HALLUCINATION_MARKERS: List[str] = [
    r"CUST\d{3}",                       # fabricated customer IDs
    r"\b\d{1,3}\.\d{1,2}%",            # fabricated percentage figures
    r"as of \d{4}",                     # fabricated date anchors
    r"(?:AED|USD|EUR)\s*[\d,]+",       # fabricated currency amounts
]

_HALLUCINATION_LABELS = [
    "Fabricated customer ID (CUST###)",
    "Fabricated percentage figure (##.##%)",
    "Fabricated date anchor (as of YYYY)",
    "Fabricated currency amount (AED/USD/EUR ###)",
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
        return EvalScore(0.0, "EMPTY_RESPONSE", "No response to evaluate", checks=[
            {"name": "Response is non-empty", "passed": False, "detail": "Empty response"},
            {"name": "Clarification-seeking language detected", "passed": False, "detail": "N/A — no response"},
            {"name": "No hallucination markers detected", "passed": False, "detail": "N/A — no response"},
        ])

    response_lower = response.lower()

    # Check for clarification-seeking language (run all, take first match)
    matched_clarification: Optional[str] = None
    for pattern in _CLARIFICATION_PATTERNS:
        if re.search(pattern, response_lower):
            matched_clarification = pattern
            break

    # Strip parenthetical format examples before hallucination check to avoid false
    # positives when the agent shows an expected-format example (e.g., CUST001) in
    # a clarifying question rather than fabricating actual customer data.
    response_for_hallucination = re.sub(r'\(e\.g\.?,\s*[^)]+\)', '', response)

    # Hallucination markers are ONLY applied when the response does NOT already
    # contain clarification-seeking language.  A clarifying response that includes
    # a policy figure for context (e.g. "floors are typically 4–5%") is NOT
    # fabricating specifics — it is grounding the clarification request.
    fired_markers: List[str] = []
    if not matched_clarification:
        for i, marker in enumerate(_HALLUCINATION_MARKERS):
            if re.search(marker, response_for_hallucination):
                fired_markers.append(_HALLUCINATION_LABELS[i])

    checks = [
        {"name": "Response is non-empty",
         "passed": True,
         "detail": f"{len(response)} characters"},
        {"name": "Clarification-seeking language detected",
         "passed": bool(matched_clarification),
         "detail": f"Pattern matched: '{matched_clarification}'" if matched_clarification
                   else "No clarification pattern found in response"},
        {"name": "No hallucination markers detected (fabricated IDs / amounts / dates)",
         "passed": not fired_markers,
         "detail": "Clean — no fabricated specifics" if not fired_markers
                   else f"Detected: {', '.join(fired_markers)}"},
    ]

    if matched_clarification:
        return EvalScore(
            1.0, "CLARIFICATION_REQUESTED",
            f"Agent asked for clarification (matched: '{matched_clarification}')",
            checks=checks,
        )

    if fired_markers:
        return EvalScore(
            0.0, "HALLUCINATED",
            f"Agent fabricated specific details without clarifying: {fired_markers[:3]}",
            checks=checks,
        )

    # Response answered but didn't hallucinate — partial credit (assumed intent)
    if expected_keywords:
        answer_lower = response.lower()
        hit_count = sum(1 for kw in expected_keywords if kw.lower() in answer_lower)
        if hit_count == 0:
            return EvalScore(
                0.0, "HALLUCINATED",
                "Response neither clarified nor addressed any expected keywords",
                checks=checks,
            )

    return EvalScore(
        0.5, "INTENT_ASSUMED",
        "Agent answered without asking for clarification — may have assumed intent correctly",
        checks=checks,
    )
