"""Answer-alignment judge (Component C / §5.5).

Confirms the generated SQL semantically answers the user's question — a check the six
safety checks do not perform (a syntactically valid query can answer the WRONG question).
Judges the SQL + result COLUMN NAMES + ROW COUNT only, never raw rows, so no regulated
data enters a second LLM call.

Shadow-first: when ``answer_validation_enabled`` is off it is a no-op that passes. When on
but not ``answer_validation_enforced``, the caller logs the verdict but still returns the
result. Never raises — on any failure it fails OPEN (passes) so it cannot block a valid
answer while un-calibrated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sql_agent.agent.prompts import ANSWER_VALIDATION_PROMPT
from sql_agent.config import settings
from sql_agent.llm import Step, get_llm, log_usage
from sql_agent.logging_config import get_logger

log = get_logger("judge")


@dataclass(frozen=True)
class Verdict:
    passes: bool
    confidence: float
    reason: str


def _parse(text: str) -> dict:
    """Extract and parse the FIRST complete, balanced JSON object in ``text``.

    Naive "first '{' to last '}'" slicing breaks the moment the model adds anything after
    the JSON — trailing commentary, a stray second brace-containing chunk, a reasoning
    model's extra remarks — because the LAST '}' in the whole response then belongs to
    that trailing content, not to the JSON object itself, and json.loads chokes on the
    leftover text ("Extra data"). Scanning brace depth from the first '{' and stopping at
    its OWN matching '}' is immune to anything that comes after.
    """
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found")
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text[start:], start):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("no balanced JSON object found")


def judge_sql(question: str, sql: str, columns: list[str], row_count: int,
              schema_context: str = "") -> Verdict:
    """Return a verdict on whether ``sql`` answers ``question``. ``schema_context`` gives
    the judge the column meanings so it doesn't have to guess from names alone."""
    if not settings.answer_validation_enabled:
        return Verdict(passes=True, confidence=1.0, reason="disabled")

    try:
        raw = get_llm(Step.JUDGE).invoke(
            ANSWER_VALIDATION_PROMPT.format(
                question=question, sql=sql,
                columns=", ".join(columns) or "(none)", row_count=row_count,
                schema_context=schema_context or "(not provided)",
            )
        )
        log_usage(Step.JUDGE, raw)
        text = raw.content if hasattr(raw, "content") else str(raw)
        data = _parse(text)
    except Exception as exc:  # noqa: BLE001 — fail open while un-calibrated
        log.warning("JUDGE failed | %s | failing open (pass)", exc)
        return Verdict(passes=True, confidence=0.0, reason="unparseable; fail-open")

    answers = bool(data.get("answers_question", True))
    confidence = float(data.get("confidence", 0.0) or 0.0)
    reason = data.get("reason", "")
    # Only treat it as a genuine failure when the model is BOTH negative AND confident;
    # a low-confidence "no" is not trusted enough to block or trigger a retry.
    passes = answers or confidence < settings.answer_validation_min_confidence
    log.info("JUDGE passes=%s answers=%s conf=%.2f | %s",
             passes, answers, confidence, reason)
    return Verdict(passes=passes, confidence=confidence, reason=reason)
