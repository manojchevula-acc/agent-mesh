"""Keyword coverage evaluator — LLM holistic topic coverage.

Replaces the per-keyword exact/semantic check grid with a single LLM call that
holistically assesses whether the response adequately covers all expected topics.

Why holistic instead of per-keyword:
  - Per-keyword checks produce N narrow check lines (one per keyword) that are
    noisy and redundant — the report shows ❌ Keyword: 'CUST001' even when the
    agent clearly addressed the customer profile.
  - Exact/stem match and even per-concept semantic checks can produce false
    negatives for IDs (CUST001 vs CUST_001), synonyms (pricing floor vs rate floor),
    or phrased answers (compliant → "meets the minimum requirement").
  - A single holistic LLM judge understands the query intent and judges whether
    the topics were addressed, not whether literal tokens matched.

Fallback (LLM unavailable): exact/stem match across all keywords, reported as a
  single broad check rather than per-keyword lines.

Scoring:
  1.0 = FULL — all expected topics covered
  0.5 = PARTIAL — some topics covered, at least one missed
  0.0 = MISSING — no expected topics covered at all
"""
from __future__ import annotations

import json
import os
import re
from typing import List, Optional

from evaluators.compliance_evaluator import EvalScore

_DEFAULT_GROQ_MODEL     = "llama-3.3-70b-versatile"
_DEFAULT_CEREBRAS_MODEL = "gemma-4-31b"

_KW_SUFFIXES = ("ing", "ed", "er", "ers", "ion", "ions", "ity", "ies", "ness", "ly", "ment", "ments", "al", "ally")

_COVERAGE_JUDGE_PROMPT = """\
You are evaluating whether a banking AI assistant (FAB — First Abu Dhabi Bank) \
adequately addressed all the key topics in its response.

USER QUERY:
{query}

EXPECTED TOPICS (key concepts the response should cover):
{topics_list}

AGENT RESPONSE:
{response}

=== Evaluation task ===
For each expected topic, decide whether the response covered it — either directly,
through a synonym, a paraphrase, or by clearly addressing the underlying idea.
A topic is COVERED if the substance is addressed, even with different words.
A topic is MISSED only if the concept is completely absent from the response.

Score:
  1.0 = FULL     — every expected topic was addressed
  0.5 = PARTIAL  — some topics addressed, at least one substantively missed
  0.0 = MISSING  — none of the expected topics addressed (e.g. error response)

Return ONLY valid JSON (no markdown fences):
{{
  "score": 1.0,
  "label": "FULL|PARTIAL|MISSING",
  "covered": ["topic_a", "topic_b"],
  "missed": [],
  "reason": "one sentence summarising the coverage verdict"
}}"""


def _call_coverage_judge(query: str, response: str, keywords: List[str]) -> Optional[dict]:
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
        topics_list = "\n".join(f"  - {kw}" for kw in keywords)
        prompt = _COVERAGE_JUDGE_PROMPT.format(
            query=query[:300],
            topics_list=topics_list,
            response=response[:1000],
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


def _stem_match(keyword: str, text: str) -> bool:
    kw = keyword.lower()
    if kw in text:
        return True
    for suffix in _KW_SUFFIXES:
        if kw.endswith(suffix) and len(kw) - len(suffix) >= 4:
            root = kw[: len(kw) - len(suffix)]
            if root in text:
                return True
    return False


def keyword_coverage_score(
    query: str,
    response: str,
    expected_keywords: List[str],
) -> EvalScore:
    """Score whether the response covers the expected key topics.

    Primary: single LLM holistic coverage verdict — one broad check, not N keyword checks.
    Fallback: exact/stem match across all keywords — one combined check.

    Args:
        query: the original user query (gives the LLM intent context)
        response: the agent's final response
        expected_keywords: list of topic strings the response should address
    """
    if not expected_keywords:
        return EvalScore(1.0, "NOT_APPLICABLE", "No expected keywords to check", checks=[
            {"name": "Expected topics defined", "passed": True,
             "detail": "No expected keywords specified for this test case"},
        ])

    if not response or not response.strip():
        return EvalScore(0.0, "MISSING", "Empty response — no topics covered", checks=[
            {"name": "Response covers expected query topics",
             "passed": False,
             "detail": "No response to evaluate"},
        ])

    # --- LLM judge (primary) ---
    llm = _call_coverage_judge(query, response, expected_keywords)

    if llm is not None:
        score   = llm.get("score", 0.0)
        label   = str(llm.get("label", "MISSING"))
        reason  = str(llm.get("reason", ""))[:200]
        covered = llm.get("covered") or []
        missed  = llm.get("missed") or []

        verdict_detail = f"{label} — {reason}"

        checks = [
            {"name": "Response covers expected query topics",
             "passed": score >= 1.0,
             "detail": verdict_detail},
        ]

        # Add a single informational check showing what was covered/missed
        # only when the verdict is PARTIAL or MISSING (i.e. there's something to flag)
        if score < 1.0:
            coverage_parts = []
            if covered:
                coverage_parts.append(f"Covered: {', '.join(str(c) for c in covered[:5])}")
            if missed:
                coverage_parts.append(f"Missed: {', '.join(str(m) for m in missed[:5])}")
            checks.append({
                "name": "Topic coverage breakdown",
                "passed": False,
                "detail": " | ".join(coverage_parts) if coverage_parts else "No detail from judge",
            })

        return EvalScore(score, label, reason or f"{int(score * len(expected_keywords))}/{len(expected_keywords)} topics covered", checks=checks)

    # --- Fallback: exact/stem match (LLM unavailable) ---
    answer_lower = response.lower()
    matched = [kw for kw in expected_keywords if _stem_match(kw, answer_lower)]
    missed  = [kw for kw in expected_keywords if not _stem_match(kw, answer_lower)]
    hit_count = len(matched)
    score = hit_count / len(expected_keywords)

    if score >= 0.75:
        label = "FULL"
    elif score > 0:
        label = "PARTIAL"
    else:
        label = "MISSING"

    detail = f"JUDGE_UNAVAILABLE — exact/stem match fallback: {hit_count}/{len(expected_keywords)} topics found"
    if missed:
        detail += f" | Missing: {missed}"

    checks = [
        {"name": "Response covers expected query topics",
         "passed": score >= 1.0,
         "detail": detail},
    ]
    if missed:
        checks.append({
            "name": "Topic coverage breakdown",
            "passed": False,
            "detail": f"Matched: {matched or 'none'} | Not matched: {missed}",
        })

    return EvalScore(score, label, f"{hit_count}/{len(expected_keywords)} topics matched", checks=checks)
