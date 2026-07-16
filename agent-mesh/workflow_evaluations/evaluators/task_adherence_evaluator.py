"""Task adherence evaluator — LLM-as-judge via Groq/Cerebras (OpenAI-compatible).

Scores whether the agent response directly addresses the banking query.
  1.0 — response directly addresses the pricing/policy/data query
  0.5 — partially on-topic (answered general question, missed specifics)
  0.0 — off-topic, refused when it shouldn't, or hallucinated a tool call
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from evaluators.compliance_evaluator import EvalScore

_EVAL_ROOT = Path(__file__).resolve().parents[1]
_MESH_ROOT = _EVAL_ROOT.parent
for _p in (str(_MESH_ROOT), str(_EVAL_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Model selection: Groq uses llama-3.3-70b-versatile; Cerebras uses llama3.1-8b
_GROQ_MODEL = "llama-3.3-70b-versatile"
_CEREBRAS_MODEL = "llama3.1-8b"

_JUDGE_PROMPT = """\
You are an objective evaluator for a banking AI assistant (FAB — First Abu Dhabi Bank).
Score the response below on a scale of 0.0, 0.5, or 1.0.

Scoring criteria:
  1.0 — The response directly and completely addresses the user's banking query
        (pricing, policy, data, or hybrid). All key aspects answered.
  0.5 — The response is partially on-topic: addressed a general aspect but missed
        specifics (e.g. answered policy in general but not for the specific customer).
  0.0 — The response is off-topic, refused without cause, hallucinated a tool call,
        or is an empty / error message.

User query: {query}

Agent response: {response}

Reply with ONLY a JSON object like: {{"score": 1.0, "reason": "..."}}
Do not include any other text."""


def task_adherence_score(
    query: str,
    response: str,
    model: Optional[str] = None,
) -> EvalScore:
    """Score task adherence via LLM-as-judge. Falls back to 0.5 on API error."""
    if not response or not response.strip():
        return EvalScore(0.0, "EMPTY_RESPONSE", "No response to evaluate")

    base_url = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    api_key = os.getenv("GROQ_API_KEY", "")

    if not api_key:
        return EvalScore(0.5, "JUDGE_UNAVAILABLE", "GROQ_API_KEY not set")

    if model is None:
        model = _CEREBRAS_MODEL if "cerebras" in base_url else _GROQ_MODEL

    try:
        return _call_judge(query, response, model, base_url, api_key)
    except Exception as exc:
        return EvalScore(0.5, "JUDGE_UNAVAILABLE", str(exc)[:120])


def _call_judge(
    query: str,
    response: str,
    model: str,
    base_url: str,
    api_key: str,
) -> EvalScore:
    import json
    from openai import OpenAI

    client = OpenAI(base_url=base_url, api_key=api_key)
    prompt = _JUDGE_PROMPT.format(
        query=query[:500],
        response=response[:1000],
    )
    message = client.chat.completions.create(
        model=model,
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.choices[0].message.content if message.choices else ""
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start == -1 or end == 0:
        return EvalScore(0.5, "JUDGE_PARSE_ERROR", raw[:80])
    data = json.loads(raw[start:end])
    score = float(data.get("score", 0.5))
    reason = str(data.get("reason", ""))
    score = max(0.0, min(1.0, score))
    if score >= 0.75:
        score = 1.0
    elif score >= 0.25:
        score = 0.5
    else:
        score = 0.0
    label = {1.0: "ADHERENT", 0.5: "PARTIAL", 0.0: "OFF_TOPIC"}[score]
    return EvalScore(score, label, reason[:200])
