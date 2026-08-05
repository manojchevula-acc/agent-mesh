"""Graded relevance judgments, derived deterministically from the gold answers.

The judgments are computed, not reviewed. A chunk is relevant when its text
contains the facts a human already wrote into ``gold_qa.json``'s
``expected_answer`` — quantities matched through the same parser stages 2b and 4
use, plus a lexical path for answers that carry no numbers.

Why this is not circular: the grader never looks at retriever output. Grading a
chunk because the retriever ranked it first would guarantee a perfect score;
grading it because it contains "180 bps" and "6.65%", which a human asserted is
the answer before any run happened, does not. That is the whole reason the input
is the gold *answer* rather than a pool of retrieved candidates.

``clause_reference`` is deliberately unused. It is derived text and is stale on a
significant share of chunks (a chunk whose body starts "## 3.2 Revolving Credit
Facilities" can carry ``clause_reference="2.4"``), which is what made the
previous label-matching deriver produce judgment sets containing title pages.
The document name from ``expected_sources`` *is* used — it is reliable, and it
is what keeps grades 2 and 3 precise.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..core.corpus import ChunkIndex, IndexedChunk
from ..core.io import read_json, write_json
from ..core.models import QrelQuestion, QrelSet, RelevanceJudgment
from ..core.numeric import match_keys, numeric_facts
from ..core.text import content_tokens

# Grade scale:
#   3  the chunk the gold answer can be read straight off
#   2  carries part of the answer, or an equally acceptable alternative source
#   1  related context — useful, does not itself contain the answer
GRADE_PRIMARY = 3
GRADE_ALTERNATE = 2
GRADE_CONTEXT = 1

# Tuned against the gold set; see the thresholds' effect in the stage report's
# "Ground-truth basis" table, which names the path taken for every question.
_FULL_NUMERIC_COVERAGE = 0.99
_FULL_COVERAGE_MIN_FACTS = 3  # Below this, a full match is too easy to hit by chance.
_STRONG_NUMERIC_COVERAGE = 0.80
_PARTIAL_NUMERIC_COVERAGE = 0.55
_STRONG_ANSWER_OVERLAP = 0.60
_PARTIAL_ANSWER_OVERLAP = 0.45
_SUPPORTING_ANSWER_OVERLAP = 0.30
_STRONG_QUESTION_OVERLAP = 0.35
_CONTEXT_QUESTION_OVERLAP = 0.50

# Function words carry no evidence but are frequent enough to lift every overlap
# ratio toward the thresholds above, which would make grade 1 fire corpus-wide.
_STOPWORDS = content_tokens(
    "the a an and or of for to in on at is are was were be been it its this that "
    "with as by from which what when where who whom how why must shall should may "
    "can will would there their they them these those any all each per not no if "
    "does do did has have had than then also such other more most under over "
    "within including include includes required require requires applicable"
)


# A unit-less small integer ("1" and "3" parsed out of "1-3yr", an article
# number, a markdown list marker) appears in almost every chunk, so counting it
# as evidence lets unrelated chunks clear the grade-2 bar on structural noise
# alone. Facts at or above this magnitude, and any fact carrying a unit, are
# distinctive enough to be worth matching on.
_WEAK_BARE_VALUE = 10


@dataclass(frozen=True)
class _Proposal:
    grade: int
    evidence: str
    basis: str


def _distinctive_facts(facts: dict[tuple[str, str], str]) -> dict[tuple[str, str], str]:
    """Keep only facts distinctive enough to identify a chunk.

    Returns empty when a gold answer carries nothing but weak integers ("Tier 1,
    Tier 2, Tier 3"). That is deliberate: falling back to matching on them would
    grade most of the corpus relevant, so such a question is better served by the
    lexical path, which an empty result routes it to.
    """

    def strong(key: tuple[str, str]) -> bool:
        unit, value = key
        if unit:
            return True
        try:
            return abs(float(value)) >= _WEAK_BARE_VALUE
        except ValueError:
            return True

    return {k: v for k, v in facts.items() if strong(k)}


def load_qrels(path: Path) -> QrelSet | None:
    raw = read_json(path)
    if raw is None:
        return None
    return QrelSet.model_validate(raw)


def save_qrels(path: Path, qrels: QrelSet) -> Path:
    return write_json(path, qrels.model_dump(mode="json"))


def _distinctive(text: str) -> set[str]:
    return {t for t in content_tokens(text, 4) if t not in _STOPWORDS}


def _overlap(needle: set[str], haystack: set[str]) -> float:
    return len(needle & haystack) / len(needle) if needle else 0.0


def _grade(
    chunk: IndexedChunk,
    *,
    gold_keys: set[tuple[str, str]],
    gold_facts: dict[tuple[str, str], str],
    answer_tokens: set[str],
    question_tokens: set[str],
    answer_keys: list[str],
    in_expected_document: bool,
) -> _Proposal | None:
    """Propose a grade for one chunk, or ``None`` for no relationship at all."""
    chunk_tokens = _distinctive(chunk.text)
    answer_overlap = _overlap(answer_tokens, chunk_tokens)
    question_overlap = _overlap(question_tokens, chunk_tokens)

    # ── Explicit human keys win: they were written for exactly this purpose ──
    if answer_keys and in_expected_document:
        body = chunk.text.casefold()
        found = [k for k in answer_keys if k.casefold() in body]
        if found:
            grade = GRADE_PRIMARY if len(found) == len(answer_keys) else GRADE_ALTERNATE
            return _Proposal(grade, "keys: " + ", ".join(found), "answer_keys")

    # ── Numeric path ────────────────────────────────────────────────────
    if gold_keys and in_expected_document:
        chunk_keys = {f.key for f in numeric_facts(chunk.text)}
        matched = match_keys(gold_keys, chunk_keys, strict_units=False)
        coverage = len(matched) / len(gold_keys)
        evidence = "facts: " + ", ".join(sorted(gold_facts[k] for k in matched)[:6])
        strong_full = coverage >= _FULL_NUMERIC_COVERAGE and len(gold_keys) >= _FULL_COVERAGE_MIN_FACTS
        strong_partial = (
            coverage >= _STRONG_NUMERIC_COVERAGE and answer_overlap >= _SUPPORTING_ANSWER_OVERLAP
        )
        if strong_full or strong_partial:
            return _Proposal(GRADE_PRIMARY, evidence, "numeric")
        if coverage >= _PARTIAL_NUMERIC_COVERAGE:
            return _Proposal(GRADE_ALTERNATE, evidence, "numeric")

    # ── Lexical path, for gold answers with no quantities ───────────────
    if not gold_keys and not answer_keys and in_expected_document:
        evidence = f"answer-term overlap {answer_overlap:.2f}"
        if answer_overlap >= _STRONG_ANSWER_OVERLAP and question_overlap >= _STRONG_QUESTION_OVERLAP:
            return _Proposal(GRADE_PRIMARY, evidence, "lexical")
        if answer_overlap >= _PARTIAL_ANSWER_OVERLAP:
            return _Proposal(GRADE_ALTERNATE, evidence, "lexical")

    # ── Context band, computed corpus-wide on purpose ───────────────────
    # Grades 2-3 stay inside the expected document because that is what keeps
    # them precise. Grade 1 does not: a chunk from another document can still be
    # legitimately on-topic, and leaving it unjudged would make precision@5 look
    # worse than it is. This is the band that shrinks unjudged_rate_at_5.
    if question_overlap >= _CONTEXT_QUESTION_OVERLAP or (
        in_expected_document and answer_overlap >= _SUPPORTING_ANSWER_OVERLAP
    ):
        return _Proposal(
            GRADE_CONTEXT,
            f"question-term overlap {question_overlap:.2f}",
            "context",
        )
    return None


def _best_effort(
    chunks: Iterable[IndexedChunk],
    *,
    gold_keys: set[tuple[str, str]],
    answer_tokens: set[str],
) -> tuple[IndexedChunk, str] | None:
    """Highest-scoring chunk in the expected document, for questions nothing hit.

    Still gold-derived — the document comes from ``expected_sources`` and the
    score from overlap with the gold answer — so this does not reintroduce
    circularity. It exists so one weakly-phrased question does not silently drop
    out of every metric.
    """
    best: tuple[float, IndexedChunk] | None = None
    for chunk in chunks:
        tokens = _distinctive(chunk.text)
        score = _overlap(answer_tokens, tokens)
        if gold_keys:
            chunk_keys = {f.key for f in numeric_facts(chunk.text)}
            score += len(match_keys(gold_keys, chunk_keys, strict_units=False)) / len(gold_keys)
        if score > 0 and (best is None or score > best[0]):
            best = (score, chunk)
    if best is None:
        return None
    return best[1], f"best available match in the expected document (score {best[0]:.2f})"


def derive(gold: list[dict[str, Any]], index: ChunkIndex) -> tuple[QrelSet, list[str]]:
    """Grade every chunk against every gold answer. Returns (qrels, warnings)."""
    warnings: list[str] = []
    questions: list[QrelQuestion] = []
    corpus = [c for c in index.chunks if not c.is_parent]

    for item in gold:
        question_id = str(item["id"])
        expected_answer = item.get("expected_answer", "") or ""
        answer_keys = [k for k in item.get("answer_keys", []) if k]
        documents = {s["document"] for s in item.get("expected_sources", [])}

        facts = _distinctive_facts({f.key: str(f) for f in numeric_facts(expected_answer)})
        gold_keys = set(facts)
        answer_tokens = _distinctive(expected_answer)
        question_tokens = _distinctive(item["question"])

        judgments: list[RelevanceJudgment] = []
        bases: set[str] = set()
        for chunk in corpus:
            proposal = _grade(
                chunk,
                gold_keys=gold_keys,
                gold_facts=facts,
                answer_tokens=answer_tokens,
                question_tokens=question_tokens,
                answer_keys=answer_keys,
                in_expected_document=chunk.document in documents,
            )
            if proposal is None:
                continue
            if proposal.grade >= GRADE_ALTERNATE:
                bases.add(proposal.basis)
            judgments.append(
                RelevanceJudgment(
                    chunk_id=chunk.chunk_id,
                    grade=proposal.grade,
                    document=chunk.document,
                    modality=chunk.modality,
                    clause_reference=chunk.clause_reference,
                    evidence=proposal.evidence,
                )
            )

        basis = "+".join(sorted(bases)) if bases else ""
        if not bases:
            fallback = _best_effort(
                [c for c in corpus if c.document in documents],
                gold_keys=gold_keys,
                answer_tokens=answer_tokens,
            )
            if fallback is not None:
                chunk, evidence = fallback
                judgments = [j for j in judgments if j.chunk_id != chunk.chunk_id]
                judgments.append(
                    RelevanceJudgment(
                        chunk_id=chunk.chunk_id,
                        grade=GRADE_ALTERNATE,
                        document=chunk.document,
                        modality=chunk.modality,
                        clause_reference=chunk.clause_reference,
                        evidence=evidence,
                    )
                )
                basis = "fallback_best"
                warnings.append(
                    f"[{question_id}] no chunk matched the gold answer; fell back to the "
                    f"closest chunk in {sorted(documents)} — the gold answer or the "
                    "extraction for this question is worth checking"
                )
            else:
                basis = "none"
                if item.get("answerable", True):
                    warnings.append(
                        f"[{question_id}] nothing in {sorted(documents)} matches the gold answer "
                        "at all; this question cannot be scored and is excluded from every metric"
                    )
                # else: expected — an "answerable": false question has no expected
                # source to match, by design. Scored by stage 4's abstention_accuracy
                # instead; see QRELS_UNANSWERABLE in the stage 3 report.

        questions.append(
            QrelQuestion(
                id=question_id,
                name=item.get("name", ""),
                question=item["question"],
                answerable=bool(item.get("answerable", True)),
                # Carried through so scoring can measure context recall against
                # the retrieved text without re-opening the gold file.
                expected_answer=expected_answer,
                answer_keys=answer_keys,
                judgments=sorted(judgments, key=lambda j: (-j.grade, j.chunk_id)),
                basis=basis,
            )
        )

    return QrelSet(questions=questions), warnings
