"""Graded relevance judgments: load, and bootstrap from the existing gold set.

``derive`` converts the legacy ``gold_qa.json`` (document + clause_reference +
modality) into chunk-id judgments by resolving each expected source against the
live index. It is a bootstrap, not a substitute for review: where a gold source
resolves to more than one chunk the question is flagged ``ambiguous`` and every
candidate is written out so a human can prune it. Nothing derived is marked
``verified``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.corpus import ChunkIndex
from ..core.io import read_json, write_json
from ..core.models import QrelQuestion, QrelSet, RelevanceJudgment

# Grade scale used by the deriver:
#   3  the chunk the gold answer was written from
#   2  an equally acceptable alternative source for the same fact
#   1  related context that is useful but does not contain the answer
GRADE_PRIMARY = 3
GRADE_ALTERNATE = 2


def load_qrels(path: Path) -> QrelSet | None:
    raw = read_json(path)
    if raw is None:
        return None
    return QrelSet.model_validate(raw)


def save_qrels(path: Path, qrels: QrelSet) -> Path:
    return write_json(path, qrels.model_dump(mode="json"))


def derive(
    gold: list[dict[str, Any]], index: ChunkIndex, existing: QrelSet | None = None
) -> tuple[QrelSet, list[str]]:
    """Resolve gold ``expected_sources`` to chunk ids. Returns (qrels, warnings)."""
    current = existing.by_id() if existing else {}
    warnings: list[str] = []
    questions: list[QrelQuestion] = []

    for item in gold:
        question_id = str(item["id"])
        previous = current.get(question_id)
        if previous is not None and previous.verified:
            questions.append(previous)  # Never clobber reviewed judgments.
            continue

        judgments: list[RelevanceJudgment] = []
        ambiguous = False
        for source in item.get("expected_sources", []):
            document = source["document"]
            clause = source.get("clause_reference")
            modality = source.get("modality")

            matches = index.find(document, clause_reference=clause, modality=modality)
            if not matches:
                # Fall back to modality-only, then document-only, so a stale
                # clause label degrades into a reviewable candidate list rather
                # than a silently empty judgment set.
                matches = index.find(document, modality=modality)
                if matches:
                    ambiguous = True
                    warnings.append(
                        f"[{question_id}] clause {clause!r} not found in {document!r}; "
                        f"fell back to {len(matches)} {modality} chunk(s) — needs review"
                    )
                else:
                    warnings.append(
                        f"[{question_id}] no chunk at all for document={document!r} "
                        f"clause={clause!r} modality={modality!r} — gold source is stale"
                    )
                    continue

            if len(matches) > 1:
                ambiguous = True
                warnings.append(
                    f"[{question_id}] document={document!r} clause={clause!r} "
                    f"matched {len(matches)} chunks; all recorded, prune by hand"
                )

            for position, chunk in enumerate(matches):
                judgments.append(
                    RelevanceJudgment(
                        chunk_id=chunk.chunk_id,
                        grade=GRADE_PRIMARY if position == 0 else GRADE_ALTERNATE,
                        document=chunk.document,
                        modality=chunk.modality,
                        clause_reference=chunk.clause_reference,
                        note="derived from gold expected_sources",
                    )
                )

        # De-duplicate, keeping the highest grade assigned to each chunk.
        best: dict[str, RelevanceJudgment] = {}
        for judgment in judgments:
            if judgment.chunk_id not in best or judgment.grade > best[judgment.chunk_id].grade:
                best[judgment.chunk_id] = judgment

        questions.append(
            QrelQuestion(
                id=question_id,
                name=item.get("name", ""),
                question=item["question"],
                answerable=bool(item.get("answerable", True)),
                judgments=sorted(best.values(), key=lambda j: (-j.grade, j.chunk_id)),
                verified=False,
                ambiguous=ambiguous,
            )
        )

    # Preserve any hand-written question absent from the gold file (e.g. an
    # unanswerable probe added only to the qrels).
    known = {q.id for q in questions}
    questions.extend(q for qid, q in current.items() if qid not in known)
    return QrelSet(questions=questions), warnings
