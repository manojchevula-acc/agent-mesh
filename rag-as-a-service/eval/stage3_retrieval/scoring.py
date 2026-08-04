"""Score a recorded retrieval run against the derived relevance judgments.

Two questions, kept separate because they fail for different reasons:

  * **Did we find the answer?**  hit rate, recall, MRR — computed over chunks
    graded "contains the answer" (grade >= 2).
  * **Was what we returned worth returning?**  precision and context precision —
    computed over chunks graded at least "related context" (grade >= 1), because
    scoring precision against answer-bearing chunks alone would cap a question
    with one answer chunk at 0.2.

``context_recall`` sits outside both: it ignores judgments entirely and asks
whether the *text* of the retrieved window contains the facts the gold answer
asserts. That is the metric that survives a grading mistake, so it is the one to
trust when a judgment looks wrong.
"""

from __future__ import annotations

from collections import Counter

from ..core.metrics import (
    context_precision_at_k,
    first_relevant_rank,
    hit_at_k,
    histogram,
    mean,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from ..core.models import QrelSet, RetrievalRun, StageReport
from ..core.numeric import match_keys, numeric_facts
from ..core.reporting import build_metric, rate, table
from ..core.thresholds import STAGE3

STAGE = "stage3_retrieval"

PRIMARY_DEPTH = 5  # What production serves (retrieval.final_top_k).
DEEP_DEPTH = 10
FIGURE_MODALITIES = {"figure", "table", "page_image"}


def _context_recall(record, question) -> float | None:
    """Share of the gold answer's facts present in the retrieved text window.

    Deterministic counterpart to RAGAS's LLM-judged ``context_recall``. Answers
    "could a perfect generator have answered from what we retrieved?" without
    any judgment of *which* chunk was supposed to carry the fact — so a grading
    error cannot flatter it, and it stays meaningful when an answer is spread
    across several chunks.
    """
    window = record.text_window(DEEP_DEPTH)
    if not window:
        return None
    if question.answer_keys:
        body = window.casefold()
        found = sum(1 for k in question.answer_keys if k.casefold() in body)
        return found / len(question.answer_keys)
    gold = {f.key for f in numeric_facts(question.expected_answer)}
    if not gold:
        return None
    return len(match_keys(gold, {f.key for f in numeric_facts(window)}, strict_units=False)) / len(gold)


def score(run: RetrievalRun, qrels: QrelSet, report: StageReport) -> None:
    by_id = qrels.by_id()
    scored = [r for r in run.records if by_id.get(r.id) and by_id[r.id].relevant_ids]

    report.meta.update(
        {
            "questions_in_run": len(run.records),
            "questions_scored": len(scored),
            "questions_unjudged": len(run.records) - len(scored),
            "grading_basis": ", ".join(
                f"{basis}={count}"
                for basis, count in sorted(Counter(q.basis or "-" for q in qrels.questions).items())
            ),
        }
    )
    if not scored:
        report.add_finding(
            "error",
            "NO_JUDGED_QUESTIONS",
            "qrels",
            "No question in the run has a relevance judgment, so nothing can be scored. "
            "Check that gold_qa.json's expected_sources name documents that exist in the "
            "live index, then re-run (judgments are derived on every run).",
        )
        return

    for question in qrels.questions:
        if question.basis == "none":
            report.add_finding(
                "warn",
                "QRELS_NO_EVIDENCE",
                question.id,
                "No chunk in the expected document contains any fact from this question's "
                "gold answer, so it is excluded from every metric. Either the gold answer "
                "is wrong or extraction lost the content — check stage 1 for this document.",
            )
        elif question.basis == "fallback_best":
            report.add_finding(
                "warn",
                "QRELS_WEAK_EVIDENCE",
                question.id,
                "Nothing met the grader's bar, so the closest chunk in the expected "
                "document was graded 2. Recall for this question is weakly grounded — add "
                "`answer_keys` to its gold entry to make the judgment explicit.",
            )

    # ── Collect per-question values ───────────────────────────────────
    hits5: list[float | None] = []
    hits10: list[float | None] = []
    recalls5: list[float | None] = []
    recalls10: list[float | None] = []
    precisions: list[float | None] = []
    ctx_precisions: list[float | None] = []
    ctx_recalls: list[float | None] = []
    reciprocals: list[float | None] = []
    unjudged = window_total = 0

    for record in scored:
        question = by_id[record.id]
        ranked = record.ranked_ids
        hits5.append(hit_at_k(ranked, question.relevant_ids, PRIMARY_DEPTH))
        hits10.append(hit_at_k(ranked, question.relevant_ids, DEEP_DEPTH))
        recalls5.append(recall_at_k(ranked, question.relevant_ids, PRIMARY_DEPTH))
        recalls10.append(recall_at_k(ranked, question.relevant_ids, DEEP_DEPTH))
        precisions.append(precision_at_k(ranked, question.useful_ids, PRIMARY_DEPTH))
        ctx_precisions.append(
            context_precision_at_k(ranked, question.useful_ids, DEEP_DEPTH)
        )
        ctx_recalls.append(_context_recall(record, question))
        reciprocals.append(reciprocal_rank(ranked, question.relevant_ids))
        window = ranked[:PRIMARY_DEPTH]
        window_total += len(window)
        unjudged += sum(1 for cid in window if cid not in question.judged_ids)

    # ── Did we find the answer? ───────────────────────────────────────
    report.add_metric(
        build_metric(
            STAGE3,
            "hit_rate_at_5",
            mean(hits5),
            detail="questions with at least one answer-bearing chunk in the served top 5",
        )
    )
    report.add_metric(
        build_metric(
            STAGE3, "hit_rate_at_10", mean(hits10), detail="same, looking 10 deep"
        )
    )
    report.add_metric(
        build_metric(
            STAGE3,
            "recall_at_5",
            mean(recalls5),
            detail="share of ALL answer-bearing chunks found, not just one",
        )
    )
    report.add_metric(build_metric(STAGE3, "recall_at_10", mean(recalls10)))
    report.add_metric(
        build_metric(
            STAGE3,
            "mrr",
            mean(reciprocals),
            detail="1/rank of the first answer-bearing chunk, averaged",
        )
    )

    # ── Was what we returned worth returning? ─────────────────────────
    report.add_metric(
        build_metric(
            STAGE3,
            "precision_at_5",
            mean(precisions),
            detail="share of the top 5 graded relevant or supporting — a LOWER BOUND, "
            "since a chunk the gold answer says nothing about is graded 0 even if useful",
        )
    )
    report.add_metric(
        build_metric(
            STAGE3,
            "context_precision_at_10",
            mean(ctx_precisions),
            detail="rank-weighted precision — the same chunks score higher when ranked "
            "earlier, unlike precision_at_5",
        )
    )
    report.add_metric(
        build_metric(
            STAGE3,
            "context_recall",
            mean(ctx_recalls),
            detail=f"share of the gold answer's facts present in the retrieved top "
            f"{DEEP_DEPTH} text — judgment-free, so a grading error cannot flatter it",
        )
    )
    report.add_metric(
        build_metric(
            STAGE3,
            "unjudged_rate_at_5",
            rate(unjudged, window_total),
            detail=f"{unjudged}/{window_total} top-5 slots the grader had no opinion on — "
            "the closer to 0, the closer precision_at_5 is to true precision",
        )
    )

    # ── Modality split ────────────────────────────────────────────────
    # The multimodal-specific risk: a VLM caption competing against prose in the
    # same embedding space. Reported as a table rather than a gate so it informs
    # without failing a build on a two-question slice.
    modality_rows: list[list[object]] = []
    for label, want_media in (("figure/table", True), ("text", False)):
        subset = [
            r for r in scored if bool(by_id[r.id].modalities & FIGURE_MODALITIES) == want_media
        ]
        if not subset:
            continue
        modality_rows.append(
            [
                label,
                len(subset),
                _fmt(mean([hit_at_k(r.ranked_ids, by_id[r.id].relevant_ids, PRIMARY_DEPTH) for r in subset])),
                _fmt(mean([recall_at_k(r.ranked_ids, by_id[r.id].relevant_ids, PRIMARY_DEPTH) for r in subset])),
                _fmt(mean([reciprocal_rank(r.ranked_ids, by_id[r.id].relevant_ids) for r in subset])),
            ]
        )
    if len(modality_rows) == 2:
        media_hit, text_hit = float(modality_rows[0][2]), float(modality_rows[1][2])
        if media_hit < text_hit - 0.10:
            report.add_finding(
                "warn",
                "MODALITY_RECALL_GAP",
                "figure vs text",
                f"Figure-answerable hit rate@5 ({media_hit:.2f}) trails text "
                f"({text_hit:.2f}) by more than 10 points — VLM captions are not competing "
                "with prose in the same embedding space.",
            )
    report.tables.append(
        table(
            "By answer modality",
            ["Answer lives in", "Questions", "Hit@5", "R@5", "MRR"],
            modality_rows,
            note="Whether captioned figures retrieve as well as prose. A gap here is an "
            "embedding or captioning problem, not a ranking one.",
        )
    )

    # ── Rank distribution ─────────────────────────────────────────────
    ranks = [first_relevant_rank(r.ranked_ids, by_id[r.id].relevant_ids) for r in scored]
    report.tables.append(
        table(
            "Rank of first answer-bearing chunk",
            ["Bucket", "Questions"],
            sorted(
                histogram(ranks, [1, 3, 5, 10]).items(),
                key=lambda kv: (kv[0] == "miss", kv[0]),
            ),
            note="An average hides the tail; this shows whether a weak MRR is "
            "'everything at rank 3' or 'most at rank 1 with four outright misses'.",
        )
    )

    # ── Per-question detail ───────────────────────────────────────────
    rows: list[list[object]] = []
    for record in scored:
        question = by_id[record.id]
        ranked = record.ranked_ids
        rank = first_relevant_rank(ranked, question.relevant_ids)
        rows.append(
            [
                record.id,
                question.name or "-",
                "media" if question.modalities & FIGURE_MODALITIES else "text",
                len(question.relevant_ids),
                rank if rank else "MISS",
                _fmt(recall_at_k(ranked, question.relevant_ids, PRIMARY_DEPTH)),
                _fmt(precision_at_k(ranked, question.useful_ids, PRIMARY_DEPTH)),
                _fmt(_context_recall(record, question)),
                f"{record.latency_ms:.0f}",
            ]
        )
    report.tables.append(
        table(
            "Per-question results",
            ["Id", "Name", "Modality", "Relevant", "First hit", "R@5", "P@5", "CtxRecall", "ms"],
            rows,
        )
    )

    # ── Ground-truth basis ────────────────────────────────────────────
    basis_rows: list[list[object]] = []
    for question in qrels.questions:
        primary = [j for j in question.judgments if j.grade == 3]
        top = primary[0] if primary else (question.judgments[0] if question.judgments else None)
        basis_rows.append(
            [
                question.id,
                question.name or "-",
                question.basis or "-",
                len(primary),
                len(question.relevant_ids),
                (top.evidence[:70] if top else "-") or "-",
            ]
        )
    report.tables.append(
        table(
            "Ground-truth basis (how each question was graded)",
            ["Id", "Name", "Basis", "Grade 3", "Relevant", "Evidence"],
            basis_rows,
            note="Judgments are derived from gold_qa.json's expected_answer, never from "
            "retriever output. `numeric` and `answer_keys` are the strong paths; `lexical` "
            "is weaker; `fallback_best` means nothing met the bar and the closest chunk was "
            "taken — add `answer_keys` to those gold entries. This is what you audit when a "
            "number looks wrong.",
        )
    )


def _fmt(value: float | None, digits: int = 3) -> str:
    return "-" if value is None else f"{value:.{digits}f}"
