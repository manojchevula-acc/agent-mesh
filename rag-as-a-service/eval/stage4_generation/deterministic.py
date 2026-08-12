"""Deterministic answer scoring — the checks that need no LLM judge.

Run these BEFORE the judge, always. They are free, they never vary between
runs, and they catch the failures a judge is worst at: a citation pointing at
a chunk that was never retrieved, a bps figure silently dropped, an answer to a
question the corpus cannot answer.

Everything here operates on data the pipeline already returns, so these can run
on cached responses without re-invoking generation.
"""

import re
from dataclasses import dataclass

from ..common import numeric
from .spans import Span, answer_body, partition

# [1]..[N] for text chunks, [I1]..[IN] for images — the contract the system
# prompt in generation/generator.py tells the model to follow.
_CITATION = re.compile(r"\[(I?)(\d+)\]")

# Phrases the generator uses when it declines. Matched on a normalised answer.
_REFUSAL_MARKERS = (
    "cannot find", "could not find", "not found in", "no information",
    "does not contain", "do not contain", "not available in", "unable to locate",
    "not covered", "no relevant", "insufficient information", "cannot answer",
    "not specified in", "outside the scope",
)


@dataclass
class CitationAudit:
    total: int
    valid: int
    invalid: list[str]  # refs pointing at nothing retrieved
    uncited_objective_spans: int

    @property
    def validity(self) -> float | None:
        return (self.valid / self.total) if self.total else None


def audit_citations(answer: str, n_chunks: int, n_images: int, spans: list[Span]) -> CitationAudit:
    """Check every [N] / [IN] resolves to something actually retrieved.

    Purely structural — it does NOT ask whether the cited chunk supports the
    claim (that needs the judge, and is scored separately as citation_support).
    A [7] when only 5 chunks came back is a dangling pointer: the user clicks
    it and gets nothing, which is an auditability failure regardless of whether
    the sentence happens to be true.

    Scored over ``answer_body`` — the same text ``partition`` uses. That
    excludes the trailing ``Sources:`` footer (which restates refs already made
    in the body, inflating the denominator) and the ``<think>`` reasoning trace
    (whose refs no user-visible sentence carries).
    """
    refs = _CITATION.findall(answer_body(answer))
    invalid = []
    for marker, number in refs:
        index = int(number)
        limit = n_images if marker == "I" else n_chunks
        # 1-based by contract; [0] is always malformed.
        if index < 1 or index > limit:
            invalid.append(f"[{marker}{number}]")

    uncited = sum(1 for s in spans if s.objective and not s.citations)
    return CitationAudit(
        total=len(refs),
        valid=len(refs) - len(invalid),
        invalid=sorted(set(invalid)),
        uncited_objective_spans=uncited,
    )


def numeric_recall(gold_answer: str, answer: str) -> tuple[float | None, list[str]]:
    """Did the answer carry every quantity the gold answer states?

    Deterministic on purpose: a judge asked whether 174 matches 180 will
    sometimes say "approximately". In pricing policy it does not.
    """
    return numeric.recall(gold_answer, answer)


def is_refusal(answer: str) -> bool:
    lowered = answer.lower()
    return any(marker in lowered for marker in _REFUSAL_MARKERS)


def score_abstention(answer: str, answerable: bool) -> bool | None:
    """Correct behaviour on unanswerable questions.

    Returns None for answerable cases — they are scored on correctness instead,
    and folding them in here would dilute the metric to meaninglessness.

    Sourced from pipeline_suite.yaml's existing out-of-corpus cases, which the
    current eval scripts carry but never score for abstention.
    """
    if answerable:
        return None
    return is_refusal(answer)


@dataclass
class DeterministicScore:
    spans: list[Span]
    citations: CitationAudit
    numeric: float | None
    numeric_missing: list[str]
    abstention_ok: bool | None
    subjective_ratio: float | None


def score(
    answer: str,
    gold_answer: str,
    n_chunks: int,
    n_images: int,
    answerable: bool = True,
) -> DeterministicScore:
    spans = partition(answer)
    total = len(spans)
    subjective = sum(1 for s in spans if s.subjective)
    num, missing = numeric_recall(gold_answer, answer) if gold_answer else (None, [])

    return DeterministicScore(
        spans=spans,
        citations=audit_citations(answer, n_chunks, n_images, spans),
        numeric=num,
        numeric_missing=missing,
        abstention_ok=score_abstention(answer, answerable),
        subjective_ratio=(subjective / total) if total else None,
    )
