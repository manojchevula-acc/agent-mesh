"""Tool output utilization evaluator.

Did the agent actually use the tool's output in its final response?

Evaluation strategy:
  Primary (LLM judge): semantic assessment — did the agent meaningfully incorporate
    the tool output into its response?  Broad, single-verdict check.
    Jaccard overlap is shown as a supporting metric alongside the LLM verdict.

  Fallback (LLM unavailable): Jaccard token overlap >= 0.15 between tool output
    and final response.  A narrow but reliable signal when LLM is not reachable.

Note on error responses: when the agent returned a generic error/fallback message,
  utilization cannot be measured — NOT_APPLICABLE (0.5) is returned immediately.
  The actual failure is already captured by Tool Call Success (TOOL_ERROR 0.00).
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

_STOP_WORDS = frozenset(
    "the a an and or but in on at to for of with is are was were be been "
    "have has had do does did will would could should may might shall".split()
)

# Phrases that identify a generic agent fallback/error response.
_AGENT_ERROR_RESPONSE_MARKERS = (
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

# ---------------------------------------------------------------------------
# LLM judge
# ---------------------------------------------------------------------------

_DEFAULT_GROQ_MODEL     = "llama-3.3-70b-versatile"
_DEFAULT_CEREBRAS_MODEL = "gemma-4-31b"

_UTILIZATION_JUDGE_PROMPT = """\
You are evaluating whether a banking AI assistant (FAB — First Abu Dhabi Bank) \
actually used the tool output it received when generating its final response.

TOOL OUTPUT (data or policy retrieved by the agent's tools):
{tool_output_preview}

AGENT FINAL RESPONSE:
{response}

=== Evaluation task ===
Did the agent meaningfully incorporate the tool output into its response?

Score:
  1.0 = OUTPUT_USED
        The response clearly draws from the tool output — specific figures, names,
        dates, policy clauses, or other details from the tool output appear in the
        response in a meaningful way.

  0.5 = OUTPUT_WEAKLY_USED
        The response is broadly consistent with the tool output but doesn't directly
        reference or quote any specific details from it — it may be paraphrasing
        very loosely or synthesising at a high level.

  0.0 = OUTPUT_NOT_USED
        The response ignores the tool output entirely — it gives generic statements,
        makes up information not present in the tool output, or addresses something
        completely different.

Return ONLY valid JSON (no markdown fences):
{{
  "score": 1.0,
  "label": "OUTPUT_USED|OUTPUT_WEAKLY_USED|OUTPUT_NOT_USED",
  "reason": "one sentence explaining the verdict",
  "evidence": "specific detail from tool output that appears in the response, or null"
}}"""


def _call_utilization_llm_judge(tool_outputs: List[str], final_response: str) -> Optional[dict]:
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        return None

    combined_tool = " | ".join(tool_outputs)
    tool_preview = combined_tool[:800]

    base_url = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    model = (
        os.getenv("EVAL_JUDGE_MODEL")
        or os.getenv("GROQ_MODEL")
        or (_DEFAULT_CEREBRAS_MODEL if "cerebras" in base_url else _DEFAULT_GROQ_MODEL)
    )
    try:
        from openai import OpenAI
        client = OpenAI(base_url=base_url, api_key=api_key)
        prompt = _UTILIZATION_JUDGE_PROMPT.format(
            tool_output_preview=tool_preview,
            response=final_response[:800],
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


def _tokenize(text: str) -> set:
    tokens = re.findall(r"[a-zA-Z0-9]+(?:\.\d+)?", text.lower())
    return {t for t in tokens if t not in _STOP_WORDS and len(t) > 1}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def tool_output_utilization_score(
    tool_outputs: List[str],
    final_response: str,
    threshold: float = 0.15,
) -> EvalScore:
    """Score whether tool outputs were meaningfully used in the final response.

    tool_outputs: list of raw output strings from DataAgent / RAGAgent hops.
    final_response: the answer returned to the user.

    Primary: LLM judge (broad semantic assessment).
    Supporting: Jaccard token overlap (shown as informational metric alongside LLM).
    Fallback: Jaccard only (when LLM unavailable).
    """
    if not tool_outputs:
        return EvalScore(1.0, "NOT_APPLICABLE", "No tool outputs to check", checks=[
            {"name": "Tool outputs available", "passed": False,
             "detail": "No tool outputs provided — evaluation not applicable"},
        ])

    if not final_response or not final_response.strip():
        return EvalScore(0.0, "NO_RESPONSE", "Empty agent response", checks=[
            {"name": "Tool outputs available", "passed": True,
             "detail": f"{len(tool_outputs)} tool output(s) available"},
            {"name": "Agent response is non-empty", "passed": False,
             "detail": "No response to evaluate"},
        ])

    # When the agent returned a generic error/fallback, utilization cannot be measured.
    # Jaccard overlap is zero by design (error text shares no domain tokens with tool data).
    # The actual failure is already captured by Tool Call Success (TOOL_ERROR 0.00).
    if any(m in final_response.lower() for m in _AGENT_ERROR_RESPONSE_MARKERS):
        return EvalScore(
            0.5, "NOT_APPLICABLE",
            "Agent returned error/fallback — output utilization check not applicable",
            checks=[
                {"name": "Tool outputs available", "passed": True,
                 "detail": f"{len(tool_outputs)} output(s)"},
                {"name": "LLM utilization verdict", "passed": True,
                 "detail": "NOT_APPLICABLE — agent returned a generic error message; "
                           "Tool Call Success evaluator captures this failure"},
            ],
        )

    # Compute Jaccard as a supporting metric regardless of which path we take
    combined_tool = " ".join(tool_outputs)
    tool_tokens = _tokenize(combined_tool)
    resp_tokens = _tokenize(final_response)
    overlap = _jaccard(tool_tokens, resp_tokens)
    jaccard_detail = f"Jaccard token overlap: {overlap:.3f}"

    # --- LLM judge (primary) ---
    llm = _call_utilization_llm_judge(tool_outputs, final_response)

    if llm is not None:
        score   = llm.get("score", 0.5)
        label   = str(llm.get("label", "OUTPUT_WEAKLY_USED"))
        reason  = str(llm.get("reason", ""))[:200]
        evidence = llm.get("evidence") or None

        verdict_detail = f"{label} — {reason}"
        if evidence:
            verdict_detail += f" | Evidence: {evidence}"

        checks = [
            {"name": "Tool outputs available",
             "passed": True,
             "detail": f"{len(tool_outputs)} output(s) provided to agent"},
            {"name": "LLM utilization verdict",
             "passed": score >= 1.0,
             "detail": verdict_detail},
            {"name": "Jaccard token overlap (supporting metric)",
             "passed": overlap >= threshold,
             "detail": f"{jaccard_detail} — {'above' if overlap >= threshold else 'below'} {threshold} threshold"},
        ]
        return EvalScore(score, label, reason, checks=checks)

    # --- Fallback: Jaccard only (LLM unavailable) ---
    used = overlap >= threshold
    weakly_used = overlap >= threshold / 2

    checks = [
        {"name": "Tool outputs available",
         "passed": True,
         "detail": f"{len(tool_outputs)} output(s)"},
        {"name": "LLM utilization verdict",
         "passed": used,
         "detail": "JUDGE_UNAVAILABLE — GROQ_API_KEY not set; using Jaccard token overlap as fallback"},
        {"name": "Jaccard token overlap",
         "passed": used,
         "detail": (f"{jaccard_detail} >= {threshold} — OUTPUT_USED" if used
                    else f"{jaccard_detail} — below {threshold} threshold")},
    ]

    if used:
        return EvalScore(1.0, "OUTPUT_USED", f"Jaccard={overlap:.3f} >= {threshold}", checks=checks)
    if weakly_used:
        return EvalScore(0.5, "OUTPUT_WEAKLY_USED", f"Jaccard={overlap:.3f} (below {threshold})", checks=checks)
    return EvalScore(0.0, "OUTPUT_NOT_USED", f"Jaccard={overlap:.3f} (threshold={threshold})", checks=checks)
