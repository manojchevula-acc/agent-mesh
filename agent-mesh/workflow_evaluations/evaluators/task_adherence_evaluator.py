"""Task adherence evaluator — LLM-as-judge via Groq/Cerebras (OpenAI-compatible).

Scores whether the agent response directly addresses the banking query.
  1.0 — response directly addresses the pricing/policy/data query
  0.5 — partially on-topic (answered general question, missed specifics)
  0.0 — off-topic, refused when it shouldn't, or hallucinated a tool call

Also exposes `semantic_keyword_check()` for the keyword coverage evaluator:
a single batched LLM call that checks whether each concept is semantically
addressed in the response (synonym-aware, paraphrase-tolerant fallback for
exact string matching).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

from evaluators.compliance_evaluator import EvalScore

_EVAL_ROOT = Path(__file__).resolve().parents[1]
_MESH_ROOT = _EVAL_ROOT.parent
for _p in (str(_MESH_ROOT), str(_EVAL_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Per-provider fallback defaults (used only when GROQ_MODEL env var is not set).
# Prefer setting GROQ_MODEL in .env to avoid hardcoded model IDs breaking on account changes.
_DEFAULT_GROQ_MODEL     = "llama-3.3-70b-versatile"
_DEFAULT_CEREBRAS_MODEL = "gemma-4-31b"

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

# Prompt for batched semantic keyword coverage check.
# The LLM is asked whether each concept is *semantically* addressed — synonyms,
# paraphrases, and domain-equivalent terms all count as a match.
_KEYWORD_CHECK_PROMPT = """\
You are checking whether a banking AI response semantically addresses a list of concepts.
A concept is COVERED if the response mentions it, uses a synonym, or clearly addresses \
the underlying idea — even with different wording.

Agent response:
{response}

For each concept listed below, reply true if covered, false if not covered.
Concepts: {concepts_json}

Reply with ONLY a JSON object mapping each concept to a boolean.
Example: {{"pricing floor": true, "provide": false}}
No other text."""


def semantic_keyword_check(
    response: str,
    keywords: List[str],
    model: Optional[str] = None,
) -> Dict[str, bool]:
    """Check whether each keyword concept is semantically covered in the response.

    Uses a single batched LLM call so N keywords cost one API round-trip.
    Returns a dict mapping each keyword to True (covered) / False (not covered).
    Falls back to all-False when the judge is unavailable, so the caller can
    treat missing keys as "not semantically matched" without crashing.
    """
    if not keywords or not response or not response.strip():
        return {kw: False for kw in keywords}

    base_url = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        return {kw: False for kw in keywords}

    if model is None:
        model = (
            os.getenv("GROQ_MODEL")
            or (_DEFAULT_CEREBRAS_MODEL if "cerebras" in base_url else _DEFAULT_GROQ_MODEL)
        )

    try:
        import json
        from openai import OpenAI

        client = OpenAI(base_url=base_url, api_key=api_key)
        concepts_json = json.dumps(keywords)
        prompt = _KEYWORD_CHECK_PROMPT.format(
            response=response[:1500],
            concepts_json=concepts_json,
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
            return {kw: False for kw in keywords}
        data = json.loads(raw[start:end])
        # Normalise keys — LLM might capitalise or add whitespace
        data_lower = {k.lower().strip(): bool(v) for k, v in data.items()}
        return {kw: data_lower.get(kw.lower().strip(), False) for kw in keywords}
    except Exception:
        return {kw: False for kw in keywords}


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
        model = (
            os.getenv("GROQ_MODEL")
            or (_DEFAULT_CEREBRAS_MODEL if "cerebras" in base_url else _DEFAULT_GROQ_MODEL)
        )

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
