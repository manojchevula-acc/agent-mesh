"""Ambiguity resolution evaluator for FAB AgentMesh.

Tests whether the agent asks for clarification when a query is vague (missing
customer ID, timeframe, product, or entity) rather than silently assuming intent.

Scoring:
  1.0 — CLARIFICATION_REQUESTED: agent explicitly asked a follow-up question
  0.5 — INTENT_ASSUMED: agent answered with an assumption (may be acceptable)
  0.0 — HALLUCINATED: agent fabricated specifics not grounded in the query

Evaluation strategy — two-tier:
  Tier 1 (deterministic): 17 regex patterns detect clarification language; 4 marker
    patterns flag potential hallucinations.  Fast and reliable for common phrasing.
  Tier 2 (LLM judge): An LLM judge runs after the deterministic pass to:
    a) catch novel clarification phrasing the regex patterns miss
       (e.g. "Kindly advise which entity you mean" → not in the 17 patterns)
    b) validate that a CLARIFICATION_REQUESTED regex match was genuinely
       asking for the right information — not superficial phrasing
    c) distinguish free-text hallucinations (invented policy statements, process
       steps, product names) from genuine partial answers
    d) assess whether an INTENT_ASSUMED verdict was a valid assumption or a mistake

  The LLM verdict is the final score when the judge is available.
  The deterministic checks are preserved in the check list for transparency.
  If GROQ_API_KEY is absent the function falls back to the deterministic result
  (labelled JUDGE_UNAVAILABLE in the checks list, not in the overall verdict).
"""
from __future__ import annotations

import json
import os
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

# ---------------------------------------------------------------------------
# LLM judge infrastructure (same Groq/Cerebras endpoint as task_adherence)
# ---------------------------------------------------------------------------

_DEFAULT_GROQ_MODEL     = "llama-3.3-70b-versatile"
_DEFAULT_CEREBRAS_MODEL = "gemma-4-31b"

_AMBIGUITY_JUDGE_PROMPT = """\
You are evaluating a banking AI assistant (FAB — First Abu Dhabi Bank) on how it \
handled an underspecified user query.

USER QUERY (underspecified — missing key information such as customer ID, product \
type, date range, or deal reference):
{query}

AGENT RESPONSE:
{response}

=== Evaluation task ===
Decide how the agent handled the ambiguity.

Score:
  1.0 = CLARIFICATION_REQUESTED
        Agent explicitly asked the user for the missing information before proceeding
        (e.g. asked for a customer ID, report type, time period, etc.).
        Even if the phrasing is polite or indirect, as long as clarification is
        genuinely being sought this scores 1.0.

  0.5 = INTENT_ASSUMED
        Agent gave a useful partial response, or stated a clear and reasonable
        assumption (e.g. "assuming you mean the current quarter…"), without
        fabricating ungrounded specifics.

  0.0 = HALLUCINATED
        Agent made up specific customer IDs, pricing figures, dates, or policy
        values that were NOT provided in the query — treating ambiguous information
        as if it were known.

Return ONLY valid JSON (no markdown fences):
{{
  "score": 1.0,
  "label": "CLARIFICATION_REQUESTED|INTENT_ASSUMED|HALLUCINATED",
  "reason": "one sentence explaining the verdict",
  "hallucination_detail": "what was hallucinated if score=0.0, else null"
}}"""


def _call_ambiguity_llm_judge(query: str, response: str) -> Optional[dict]:
    """Call the LLM judge for ambiguity resolution.

    Returns a parsed dict with keys: score, label, reason, hallucination_detail.
    Returns None when GROQ_API_KEY is absent or an exception occurs so the caller
    can fall back to the deterministic result gracefully.
    """
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        return None

    base_url = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    model = (
        os.getenv("EVAL_JUDGE_MODEL")
        or os.getenv("GROQ_MODEL")
        or (_DEFAULT_CEREBRAS_MODEL if "cerebras" in base_url else _DEFAULT_GROQ_MODEL)
    )
    try:
        from openai import OpenAI
        client = OpenAI(base_url=base_url, api_key=api_key)
        prompt = _AMBIGUITY_JUDGE_PROMPT.format(
            query=query[:400],
            response=response[:900],
        )
        resp = client.chat.completions.create(
            model=model,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.choices[0].message.content if resp.choices else ""
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start == -1 or end <= 0:
            return None
        data = json.loads(raw[start:end])
        # Normalise score to 0.0 / 0.5 / 1.0
        raw_score = float(data.get("score", 0.5))
        if raw_score >= 0.75:
            data["score"] = 1.0
        elif raw_score >= 0.25:
            data["score"] = 0.5
        else:
            data["score"] = 0.0
        return data
    except Exception:
        return None


def ambiguity_resolution_score(
    query: str,
    response: str,
    expected_keywords: Optional[List[str]] = None,
) -> EvalScore:
    """Score whether the agent handled an ambiguous query appropriately.

    Two-tier evaluation:
      1. Deterministic: regex patterns for clarification language and hallucination
         markers (fast, reliable for common phrasing).
      2. LLM judge: semantic assessment of clarification quality, novel phrasings
         not covered by the 17 regex patterns, and free-text hallucination detection.

    The LLM verdict is the final score when the judge is reachable.
    Deterministic checks are always shown in the report for transparency.

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
            {"name": "LLM judge verdict", "passed": False, "detail": "N/A — no response"},
        ])

    response_lower = response.lower()

    # ---- Tier 1: Deterministic ----
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
    # contain clarification-seeking language.
    fired_markers: List[str] = []
    if not matched_clarification:
        for i, marker in enumerate(_HALLUCINATION_MARKERS):
            if re.search(marker, response_for_hallucination):
                fired_markers.append(_HALLUCINATION_LABELS[i])

    # Derive deterministic verdict (used as fallback if LLM unavailable)
    if matched_clarification:
        det_score, det_label, det_detail = (
            1.0, "CLARIFICATION_REQUESTED",
            f"Pattern matched: '{matched_clarification}'",
        )
    elif fired_markers:
        det_score, det_label, det_detail = (
            0.0, "HALLUCINATED",
            f"Markers fired: {', '.join(fired_markers[:3])}",
        )
    elif expected_keywords:
        hit_count = sum(1 for kw in expected_keywords if kw.lower() in response_lower)
        if hit_count == 0:
            det_score, det_label, det_detail = (
                0.0, "HALLUCINATED",
                "Response neither clarified nor addressed any expected keywords",
            )
        else:
            det_score, det_label, det_detail = (
                0.5, "INTENT_ASSUMED",
                "Agent answered without asking for clarification",
            )
    else:
        det_score, det_label, det_detail = (
            0.5, "INTENT_ASSUMED",
            "Agent answered without asking for clarification — may have assumed intent correctly",
        )

    # Build deterministic check entries (always shown)
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

    # ---- Tier 2: LLM judge ----
    llm_result = _call_ambiguity_llm_judge(query, response)

    if llm_result is None:
        # LLM unavailable — fall back to deterministic verdict
        checks.append({
            "name": "LLM judge verdict",
            "passed": det_score >= 1.0,
            "detail": "JUDGE_UNAVAILABLE — GROQ_API_KEY not set or judge unreachable; "
                      "falling back to deterministic result",
        })
        return EvalScore(
            det_score, det_label,
            f"Agent asked for clarification (matched: '{matched_clarification}')"
            if matched_clarification else det_detail,
            checks=checks,
        )

    llm_score  = llm_result.get("score", det_score)
    llm_label  = str(llm_result.get("label", det_label))
    llm_reason = str(llm_result.get("reason", ""))[:200]
    llm_detail = llm_result.get("hallucination_detail") or None

    llm_check_detail = f"{llm_label} — {llm_reason}"
    if llm_detail:
        llm_check_detail += f" | Hallucination: {llm_detail}"

    checks.append({
        "name": "LLM judge verdict",
        "passed": llm_score >= 1.0,
        "detail": llm_check_detail,
    })

    # LLM is the primary verdict; deterministic checks are shown for transparency
    final_score = llm_score
    final_label = llm_label
    final_detail = llm_reason or (
        f"Agent asked for clarification (matched: '{matched_clarification}')"
        if matched_clarification else det_detail
    )

    return EvalScore(final_score, final_label, final_detail, checks=checks)
