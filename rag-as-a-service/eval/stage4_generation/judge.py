"""LLM judge for answer correctness, with bounded concurrency and retries.

Judging is separated from generation on purpose: generation is expensive and
mostly stable, judging is cheap and its prompt gets iterated on. Recording the
answers once and re-judging them many times is the difference between a
five-minute loop and an hour-long one.

The judge grades whether the facts *stated* are correct, not whether the answer
restates every detail of the gold answer — a gold answer commonly carries extra
context the question never asked for, and penalising its omission would measure
verbosity rather than accuracy.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from gernas_rag.llm.base import BaseLLM, Message

VERDICTS = ("CORRECT", "PARTIALLY_CORRECT", "INCORRECT")
UNKNOWN = "UNKNOWN"

JUDGE_SYSTEM_PROMPT = (
    "You are a strict grading assistant for a RAG system evaluation. You will be given a "
    "QUESTION, a GOLD ANSWER (reference facts), and an AGENT ANSWER produced by the system "
    "under test.\n\n"
    "Grade the AGENT ANSWER on whether the facts it states are CORRECT, not on whether it "
    "restates every detail present in the GOLD ANSWER. The GOLD ANSWER may include "
    "supplementary facts beyond what the QUESTION literally asks — do not penalise the AGENT "
    "ANSWER for omitting a fact the QUESTION did not ask for. Ignore differences in phrasing, "
    "formatting, citation markers, and minor rounding or unit formatting.\n\n"
    "- CORRECT: every fact the AGENT ANSWER states in response to the QUESTION is accurate and "
    "consistent with the GOLD ANSWER. Omitting an unrequested detail is still CORRECT.\n"
    "- PARTIALLY_CORRECT: the AGENT ANSWER addresses the question but gets at least one fact "
    "wrong, hedges instead of answering, or omits a fact the QUESTION explicitly asked for.\n"
    "- INCORRECT: the AGENT ANSWER contradicts the GOLD ANSWER on the core fact(s) asked, "
    "fabricates information, or fails to answer the question at all.\n\n"
    "Numbers are the point of this corpus: a wrong figure, unit or date is never CORRECT.\n\n"
    "Respond in EXACTLY this format, nothing else:\n"
    "VERDICT: <CORRECT|PARTIALLY_CORRECT|INCORRECT>\n"
    "REASON: <one or two sentences>"
)

_VERDICT_RE = re.compile(r"VERDICT:\s*(CORRECT|PARTIALLY_CORRECT|INCORRECT)", re.IGNORECASE)
_REASON_RE = re.compile(r"REASON:\s*(.+)", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class Judgment:
    question_id: str
    verdict: str
    reason: str
    raw: str = ""

    @property
    def is_pass_strict(self) -> bool:
        return self.verdict == "CORRECT"

    @property
    def is_pass_lenient(self) -> bool:
        return self.verdict in ("CORRECT", "PARTIALLY_CORRECT")


def parse_verdict(raw: str) -> tuple[str, str]:
    """Extract (verdict, reason). Unparseable output becomes UNKNOWN, never a pass.

    An unparsed judge response must not be silently counted as a failure *or* a
    success — it is missing data, and its rate is gated separately so a flaky
    judge shows up as a judge problem rather than as a quality change.
    """
    verdict_match = _VERDICT_RE.search(raw or "")
    reason_match = _REASON_RE.search(raw or "")
    return (
        verdict_match.group(1).upper() if verdict_match else UNKNOWN,
        (reason_match.group(1).strip() if reason_match else (raw or "").strip())[:600],
    )


class AnswerJudge:
    """Grades recorded answers against gold answers."""

    def __init__(self, llm: BaseLLM, max_concurrent: int = 3, max_attempts: int = 3) -> None:
        self._llm = llm
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_attempts = max_attempts

    async def judge_one(
        self, question_id: str, question: str, gold_answer: str, agent_answer: str
    ) -> Judgment:
        messages = [
            Message(role="system", content=JUDGE_SYSTEM_PROMPT),
            Message(
                role="user",
                content=(
                    f"QUESTION: {question}\n\n"
                    f"GOLD ANSWER: {gold_answer}\n\n"
                    f"AGENT ANSWER: {agent_answer}"
                ),
            ),
        ]
        last_error = ""
        async with self._semaphore:
            for attempt in range(1, self._max_attempts + 1):
                try:
                    raw = await self._llm.generate(messages)
                except Exception as exc:  # noqa: BLE001 — retry transient provider errors.
                    last_error = str(exc)
                    if attempt == self._max_attempts:
                        break
                    await asyncio.sleep(2.0 * attempt)
                    continue
                verdict, reason = parse_verdict(raw)
                if verdict != UNKNOWN:
                    return Judgment(question_id, verdict, reason, raw)
                last_error = "judge response did not contain a parseable VERDICT line"
                if attempt == self._max_attempts:
                    return Judgment(question_id, UNKNOWN, last_error, raw)
        return Judgment(question_id, UNKNOWN, f"judge call failed: {last_error}")

    async def judge_all(self, items: list[tuple[str, str, str, str]]) -> list[Judgment]:
        """Judge ``(id, question, gold_answer, agent_answer)`` tuples concurrently."""
        return list(await asyncio.gather(*(self.judge_one(*item) for item in items)))
