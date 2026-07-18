"""Task completion evaluator — LLM-as-judge primary, deterministic fallback.

Verifies that the task was actually completed, not just attempted.

Evaluation strategy:
  Primary (LLM judge): The LLM receives the query, response, and route type and
    makes a broad semantic judgment about whether the task was fully completed.
    It returns a score plus up to 3 broad dimension-level checks that explain
    its reasoning — these are surfaced directly in the report.

  Fallback (no LLM): A minimal structural check confirms whether the response
    looks substantive (not an error/empty message) appropriate for the route type.
    This is intentionally broad — one check, not a battery of narrow regex signals.

Route types:
  data    — response should contain specific customer/financial data
  knowledge — response should explain policy and cite a document
  hybrid  — response should contain BOTH data AND policy citation
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import List, Optional

from evaluators.compliance_evaluator import EvalScore

_EVAL_ROOT = Path(__file__).resolve().parents[1]
if str(_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_ROOT))

# ---------------------------------------------------------------------------
# LLM judge
# ---------------------------------------------------------------------------

_DEFAULT_GROQ_MODEL     = "llama-3.3-70b-versatile"
_DEFAULT_CEREBRAS_MODEL = "gemma-4-31b"

_TASK_COMPLETION_JUDGE_PROMPT = """\
You are evaluating a banking AI assistant (FAB — First Abu Dhabi Bank) on whether \
it fully completed the user's task.

ORIGINAL USER QUERY:
{query}

AGENT RESPONSE:
{response}

TASK TYPE: {route_type}
{route_type_guidance}

=== Your job ===
1. Score whether the agent fully completed the task.
2. For each of the 3 evaluation dimensions below, state whether it was addressed.

DIMENSIONS (tailor to task type):
  A. "Query directly answered" — Did the agent directly address what was asked \
(not deflect, not give a generic error, not ask a question back)?
  B. "Content appropriate for task type" — For DATA: were specific numbers/figures/\
records returned? For KNOWLEDGE: was policy/regulation explained with a citation? \
For HYBRID: were both present?
  C. "Response is substantive" — Is the response meaningfully detailed (not a \
one-liner that skips the actual answer, not a generic fallback)?

SCORING:
  1.0 = COMPLETE     — All 3 dimensions fully addressed
  0.5 = PARTIAL      — Some dimensions addressed but key content missing
  0.0 = INCOMPLETE   — Task not completed (error response, off-topic, no content)

Return ONLY valid JSON (no markdown fences, no extra keys):
{{
  "score": 1.0,
  "label": "COMPLETE|PARTIAL|INCOMPLETE",
  "dim_a": {{"passed": true, "detail": "one sentence"}},
  "dim_b": {{"passed": true, "detail": "one sentence"}},
  "dim_c": {{"passed": true, "detail": "one sentence"}},
  "overall_reason": "one sentence summarising the verdict"
}}"""

_ROUTE_GUIDANCE = {
    "data": (
        "DATA task — the agent should return specific customer or financial data: "
        "numbers, percentages, currency amounts, account balances, exposure figures, "
        "pricing rates, customer attributes, or similar quantitative / structured information."
    ),
    "knowledge": (
        "KNOWLEDGE task — the agent should explain a policy, regulation, or guideline "
        "and cite the relevant document (e.g. FAB Credit Pricing Policy, CBUAE circular, "
        "Basel III framework, AML/KYC policy).  A complete answer includes the policy "
        "substance AND the source reference."
    ),
    "hybrid": (
        "HYBRID task — the agent must BOTH return specific customer data AND provide "
        "policy context with a citation.  Addressing only one half is PARTIAL."
    ),
}

# Error/fallback markers — when the agent returned one of these, the task is
# clearly not completed regardless of route type.
_ERROR_MARKERS = (
    "i was unable to retrieve",
    "unable to retrieve the required data",
    "please try again",
    "contact your relationship manager",
    "an error occurred",
    "could not retrieve",
    "failed to retrieve",
    "service is currently unavailable",
    "i'm unable to retrieve",
    "i am unable to retrieve",
)


def _call_task_completion_judge(query: str, response: str, route_type: str) -> Optional[dict]:
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
        prompt = _TASK_COMPLETION_JUDGE_PROMPT.format(
            query=query[:400] if query else "(query not provided)",
            response=response[:900],
            route_type=route_type.upper(),
            route_type_guidance=_ROUTE_GUIDANCE.get(route_type, ""),
        )
        resp = client.chat.completions.create(
            model=model,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.choices[0].message.content if resp.choices else ""
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start == -1 or end <= 0:
            return None
        data = json.loads(raw[start:end])
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


def task_completion_score(
    response: str,
    route_type: str,
    query: str = "",
) -> EvalScore:
    """Score task completion using LLM semantic judgment.

    Primary: LLM judge evaluates 3 broad dimensions and returns structured checks.
    Fallback: structural check (is the response substantive, not an error).

    route_type: "data" | "knowledge" | "hybrid"
    query: original user query (improves LLM judge accuracy; optional for back-compat)
    """
    if not response or not response.strip():
        return EvalScore(0.0, "EMPTY_RESPONSE", "Empty response — task not completed", checks=[
            {"name": "Response is non-empty", "passed": False,
             "detail": "No response returned by the agent"},
        ])

    if route_type not in ("data", "knowledge", "hybrid"):
        return EvalScore(1.0, "NOT_APPLICABLE", f"Route type '{route_type}' — task completion not scored", checks=[
            {"name": f"Route type '{route_type}' — task completion check not applicable",
             "passed": True,
             "detail": "Blocked or unclassified routes are excluded from task completion scoring"},
        ])

    # --- LLM judge (primary) ---
    llm = _call_task_completion_judge(query, response, route_type)

    if llm is not None:
        score   = llm.get("score", 0.5)
        label   = str(llm.get("label", "PARTIAL"))
        reason  = str(llm.get("overall_reason", ""))[:200]

        def _dim(key: str, default_name: str) -> dict:
            d = llm.get(key, {})
            return {
                "name": default_name,
                "passed": bool(d.get("passed", False)),
                "detail": str(d.get("detail", ""))[:200],
            }

        checks = [
            _dim("dim_a", "Query directly answered"),
            _dim("dim_b", f"Content appropriate for '{route_type}' task type"),
            _dim("dim_c", "Response is substantive (not an error or generic fallback)"),
        ]

        return EvalScore(score, label, reason, checks=checks)

    # --- Fallback: structural check (LLM unavailable) ---
    is_error = any(m in response.lower() for m in _ERROR_MARKERS)
    is_substantive = len(response.strip()) > 80 and not is_error

    fallback_checks = [
        {"name": "Response is non-empty and substantive",
         "passed": is_substantive,
         "detail": ("Response appears substantive for this task type"
                    if is_substantive
                    else "Response is an error message or too short to be a valid answer")},
        {"name": "LLM completion judge verdict",
         "passed": is_substantive,
         "detail": "JUDGE_UNAVAILABLE — GROQ_API_KEY not set; using structural fallback check"},
    ]

    if is_error:
        return EvalScore(0.0, "INCOMPLETE",
                         "Agent returned an error/fallback message — task not completed",
                         checks=fallback_checks)
    if is_substantive:
        return EvalScore(1.0, "COMPLETE",
                         "Response appears substantive — LLM judge unavailable for deeper assessment",
                         checks=fallback_checks)
    return EvalScore(0.5, "PARTIAL",
                     "Response present but may be insufficient — LLM judge unavailable",
                     checks=fallback_checks)
