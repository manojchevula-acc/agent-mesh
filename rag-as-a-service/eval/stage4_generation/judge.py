"""LLM judge for answer quality — the parts a rule cannot decide.

Everything deterministic already ran in deterministic.py. What is left genuinely
needs a reader: whether a span is supported by the context, whether the answer
addresses the question asked, whether a citation backs the claim beside it.

JUDGE INDEPENDENCE: the judge must not be either generator. A model grading its
own output inflates every score with no visible failure. This ports the refusal
from scripts/eval_llm_judge.py rather than reinventing it, so the two agree.

Per-span, not per-answer. This is RAG-Check's CS: the paper scores each atomic
statement so one wrong clause cannot hide inside a mostly-right answer — the
exact failure eval_runs.md case 4 records as a bare "1/2".
"""

import json
import sys

sys.path.insert(0, "src")

from gernas_rag.llm.base import Message  # noqa: E402
from gernas_rag.llm.factory import get_llm  # noqa: E402

from .spans import Span  # noqa: E402

DEFAULT_JUDGE = "llama-3.3-70b-versatile"


class JudgeCollisionError(RuntimeError):
    """Raised when the judge model is also a generator."""


def build_judge(settings, judge_model: str | None = None):
    """Return (llm, model_name), refusing if the judge is also a generator."""
    model = judge_model or settings.evaluation.judge_model or DEFAULT_JUDGE
    generators = {settings.llm.model_name}
    if settings.llm.vision_enabled:
        generators.add(settings.llm.vision_model_name)

    if model in generators:
        raise JudgeCollisionError(
            f"judge model {model!r} is also a GENERATOR ({sorted(generators)}). "
            "A model grading its own output inflates every score with no visible "
            f"failure. Pass --judge-model {DEFAULT_JUDGE} or set "
            "RAG__EVALUATION__JUDGE_MODEL to a third model."
        )

    config = settings.llm.model_copy(update={"model_name": model, "vision_enabled": False})
    return get_llm(config), model


_SPAN_SYSTEM = (
    "You evaluate a retrieval-augmented generation system for banking policy "
    "documents. You are given the CONTEXT the system retrieved and a numbered "
    "list of STATEMENTS taken from its answer.\n\n"
    "Judge ONLY against the context. Do not use outside banking knowledge. A "
    "number that differs from the context is WRONG, not 'approximately right'.\n\n"
    "Return ONLY a JSON object:\n"
    '  "spans": [ {"n": <statement number>, '
    '"supported": true|false, '
    '"reason": "<short>"} ]\n\n'
    '"supported" means the CONTEXT contains the information asserted. If the '
    "statement asserts specifics that are absent from the context, it is false "
    "even if it sounds plausible or happens to be true in the real world."
)

_ANSWER_SYSTEM = (
    "You evaluate a RAG answer for banking policy Q&A. You are given the "
    "QUESTION, the GOLD ANSWER (authoritative), and the system's ANSWER.\n\n"
    "Return ONLY a JSON object:\n"
    '  "answer_correct": 0|1|2  — 2 = matches the gold answer on every material '
    "fact; 1 = partially correct or missing material facts; 0 = wrong, or "
    "declined when the gold answer exists.\n"
    '  "answer_relevant": true|false — does the answer ADDRESS the question '
    "that was asked? An answer can be factually true and still fail this by "
    "answering a different question.\n"
    '  "missing_facts": [ ... ]\n'
    '  "reason": "one sentence"\n'
)


def _parse(raw: str) -> dict:
    text = raw.strip()
    if "```" in text:  # strip fences some models add
        text = text.split("```")[1].lstrip("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return {"error": "judge returned no JSON", "raw": raw[:300]}
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        return {"error": f"bad JSON: {exc}", "raw": raw[:300]}


async def judge_spans(llm, context: str, spans: list[Span]) -> dict[int, dict]:
    """Score every OBJECTIVE span for support by the context.

    Subjective spans are never sent: "the fee is likely around 50 bps" has no
    truth value to check, and asking anyway produces noise that pollutes the
    groundedness rate.

    All spans go in ONE call. Per-span calls would multiply cost by ~5x and,
    worse, deny the judge the surrounding statements it needs to resolve
    references between them.
    """
    objective = [(i, s) for i, s in enumerate(spans) if s.objective]
    if not objective:
        return {}

    listing = "\n".join(f"{n}. {s.text}" for n, (_i, s) in enumerate(objective, 1))
    raw = await llm.generate(
        [
            Message(role="system", content=_SPAN_SYSTEM),
            Message(role="user", content=f"CONTEXT:\n{context}\n\nSTATEMENTS:\n{listing}"),
        ]
    )
    verdict = _parse(raw)
    if verdict.get("error"):
        return {"error": verdict}  # type: ignore[dict-item]

    out: dict[int, dict] = {}
    for item in verdict.get("spans", []):
        try:
            position = int(item["n"]) - 1
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= position < len(objective):
            out[objective[position][0]] = item
    return out


async def judge_answer(llm, question: str, gold: str, answer: str) -> dict:
    return _parse(
        await llm.generate(
            [
                Message(role="system", content=_ANSWER_SYSTEM),
                Message(
                    role="user",
                    content=(
                        f"QUESTION:\n{question}\n\nGOLD ANSWER:\n{gold}\n\n"
                        f"ANSWER:\n{answer}"
                    ),
                ),
            ]
        )
    )
