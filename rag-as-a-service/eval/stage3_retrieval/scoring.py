"""Score recorded retrieval runs: recall, ordering, ablation and modality slices.

Gated metrics are computed on the ``final`` stage — the ordering production
actually serves. The other stages appear in the ablation table, which is what
tells you *where* a drop happened: a big gap between ``rrf`` and ``rerank``
means the cross-encoder is the problem, not the embeddings.
"""

from __future__ import annotations

from ..core.metrics import (
    count_demotions,
    first_relevant_rank,
    histogram,
    mean,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from ..core.models import QrelSet, RetrievalRun, StageReport
from ..core.reporting import build_metric, rate, table
from ..core.thresholds import STAGE3
from .runner import STAGE_ORDER

STAGE = "stage3_retrieval"

RECALL_DEPTHS = (1, 3, 5, 10)
NDCG_DEPTH = 10
FIGURE_MODALITIES = {"figure", "table", "page_image"}


def _stage_metrics(run: RetrievalRun, qrels: QrelSet, stage: str) -> dict[str, float | None]:
    """Aggregate ranking metrics for one pipeline stage across all questions."""
    by_id = qrels.by_id()
    recalls: dict[int, list[float | None]] = {k: [] for k in RECALL_DEPTHS}
    reciprocals: list[float | None] = []
    ndcgs: list[float | None] = []
    precisions: list[float | None] = []

    for record in run.records:
        question = by_id.get(record.id)
        stage_result = record.stages.get(stage)
        if question is None or stage_result is None or not question.relevant_ids:
            continue
        ranked = stage_result.ranked_ids
        for depth in RECALL_DEPTHS:
            recalls[depth].append(recall_at_k(ranked, question.relevant_ids, depth))
        reciprocals.append(reciprocal_rank(ranked, question.relevant_ids))
        ndcgs.append(ndcg_at_k(ranked, question.graded, NDCG_DEPTH))
        precisions.append(precision_at_k(ranked, question.relevant_ids, 5))

    out: dict[str, float | None] = {f"recall_at_{k}": mean(v) for k, v in recalls.items()}
    out["mrr"] = mean(reciprocals)
    out[f"ndcg_at_{NDCG_DEPTH}"] = mean(ndcgs)
    out["precision_at_5"] = mean(precisions)
    return out


def _slice_recall(run: RetrievalRun, qrels: QrelSet, want_media: bool, depth: int = 5) -> tuple[float | None, int]:
    """Recall@depth restricted to questions whose answer lives in media (or text)."""
    by_id = qrels.by_id()
    values: list[float | None] = []
    for record in run.records:
        question = by_id.get(record.id)
        final = record.stages.get("final")
        if question is None or final is None or not question.relevant_ids:
            continue
        is_media_question = bool(question.modalities & FIGURE_MODALITIES)
        if is_media_question != want_media:
            continue
        values.append(recall_at_k(final.ranked_ids, question.relevant_ids, depth))
    return mean(values), len(values)


def score(run: RetrievalRun, qrels: QrelSet, report: StageReport) -> None:
    by_id = qrels.by_id()
    scored = [r for r in run.records if by_id.get(r.id) and by_id[r.id].relevant_ids]

    report.meta.update(
        {
            "questions_in_run": len(run.records),
            "questions_scored": len(scored),
            "questions_unjudged": len(run.records) - len(scored),
            "verified_qrels": sum(1 for q in qrels.questions if q.verified),
        }
    )
    if not scored:
        report.add_finding(
            "error",
            "NO_JUDGED_QUESTIONS",
            "qrels",
            "No question in the run has a relevance judgment, so nothing can be scored. "
            "Derive qrels first (`--derive-qrels`) and review them.",
        )
        return

    for question in qrels.questions:
        if question.ambiguous and not question.verified:
            report.add_finding(
                "warn",
                "QRELS_AMBIGUOUS",
                question.id,
                "A gold source resolved to more than one chunk when these judgments were "
                "derived, and they have not been reviewed. Recall may be overstated.",
            )
    unverified = sum(1 for q in qrels.questions if not q.verified)
    if unverified:
        report.add_finding(
            "warn",
            "QRELS_UNVERIFIED",
            f"{unverified}/{len(qrels.questions)}",
            "Judgments derived automatically from the gold set have not been reviewed by "
            "a human. Treat the numbers as indicative until they are.",
        )

    # ── Gated metrics on the served ordering ──────────────────────────
    final_metrics = _stage_metrics(run, qrels, "final")
    for name in ("recall_at_5", "recall_at_10", "mrr"):
        report.add_metric(build_metric(STAGE3, name, final_metrics.get(name)))
    report.add_metric(
        build_metric(
            STAGE3,
            "ndcg_at_10",
            final_metrics.get("ndcg_at_10"),
            detail="the ordering metric — changes when a correct chunk moves rank",
        )
    )

    media_recall, media_count = _slice_recall(run, qrels, want_media=True)
    text_recall, text_count = _slice_recall(run, qrels, want_media=False)
    report.add_metric(
        build_metric(
            STAGE3,
            "recall_at_5_figure",
            media_recall,
            detail=f"{media_count} question(s) answerable from a figure/table chunk",
        )
    )
    report.add_metric(
        build_metric(
            STAGE3,
            "recall_at_5_text",
            text_recall,
            detail=f"{text_count} text-only question(s)",
        )
    )
    if media_recall is not None and text_recall is not None and media_recall < text_recall - 0.1:
        report.add_finding(
            "warn",
            "MODALITY_RECALL_GAP",
            "figure vs text",
            f"Figure-answerable recall@5 ({media_recall:.2f}) trails text recall@5 "
            f"({text_recall:.2f}) by more than 10 points — VLM captions are not competing "
            "with prose in the same embedding space.",
        )

    # ── Reranker regression ───────────────────────────────────────────
    demotions = drops = 0
    demotion_rows: list[list[object]] = []
    for record in scored:
        question = by_id[record.id]
        before = record.stages.get("rrf")
        after = record.stages.get("rerank")
        if before is None or after is None:
            continue
        demoted, dropped = count_demotions(
            before.ranked_ids, after.ranked_ids, question.relevant_ids
        )
        demotions += demoted
        drops += dropped
        if dropped:
            report.add_finding(
                "error",
                "RERANK_DROPPED_RELEVANT",
                record.id,
                f"The cross-encoder pushed {dropped} known-relevant chunk(s) out of the "
                "result window entirely. Cross-encoders are trained on prose; transcribed "
                "captions do not look like prose.",
            )
        if demoted or dropped:
            demotion_rows.append([record.id, question.name, demoted, dropped])

    report.add_metric(
        build_metric(STAGE3, "rerank_demotion_count", float(demotions), unit="count")
    )
    report.add_metric(build_metric(STAGE3, "rerank_drop_count", float(drops), unit="count"))
    if demotion_rows:
        report.tables.append(
            table(
                "Reranker movements on relevant chunks (rrf -> rerank)",
                ["Question", "Name", "Demoted", "Dropped"],
                demotion_rows,
            )
        )

    # ── Ablation ──────────────────────────────────────────────────────
    ablation_rows: list[list[object]] = []
    for stage in STAGE_ORDER:
        metrics = _stage_metrics(run, qrels, stage)
        ablation_rows.append(
            [
                stage,
                _fmt(metrics.get("recall_at_1")),
                _fmt(metrics.get("recall_at_3")),
                _fmt(metrics.get("recall_at_5")),
                _fmt(metrics.get("recall_at_10")),
                _fmt(metrics.get("mrr")),
                _fmt(metrics.get("ndcg_at_10")),
                _fmt(
                    mean(
                        [
                            r.stages[stage].latency_ms
                            for r in run.records
                            if stage in r.stages
                        ]
                    ),
                    digits=1,
                ),
            ]
        )
    report.tables.append(
        table(
            "Stage ablation",
            ["Stage", "R@1", "R@3", "R@5", "R@10", "MRR", "nDCG@10", "Latency ms"],
            ablation_rows,
            note="Read the deltas, not the absolutes. A large R@10-vs-R@3 gap is a ranking "
            "problem (fix the reranker); a low R@10 everywhere is a recall problem (fix "
            "embeddings, chunking or extraction).",
        )
    )

    # ── Rank distribution ─────────────────────────────────────────────
    ranks = [
        first_relevant_rank(record.stages["final"].ranked_ids, by_id[record.id].relevant_ids)
        for record in scored
        if "final" in record.stages
    ]
    distribution = histogram(ranks, [1, 3, 5, 10])
    report.tables.append(
        table(
            "Rank of first relevant chunk (final ordering)",
            ["Bucket", "Questions"],
            sorted(distribution.items(), key=lambda kv: (kv[0] == "miss", kv[0])),
            note="An average rank hides the tail; this shows whether a weak MRR is "
            "'everything at rank 3' or 'most at rank 1 with four outright misses'.",
        )
    )

    # ── Pipeline parity ───────────────────────────────────────────────
    checked = [r for r in run.records if r.pipeline_parity is not None]
    matching = sum(1 for r in checked if r.pipeline_parity)
    report.add_metric(
        build_metric(
            STAGE3,
            "pipeline_parity_rate",
            rate(matching, len(checked)),
            detail=f"{matching}/{len(checked)} questions where the reproduced final "
            "ordering matched RetrievalPipeline.retrieve()",
        )
    )
    for record in checked:
        if not record.pipeline_parity:
            report.add_finding(
                "error",
                "PIPELINE_PARITY_BROKEN",
                record.id,
                "The ablation's reproduced ordering no longer matches the real pipeline, "
                "so every stage 3 number is measuring something production does not do. "
                "Re-sync eval/stage3_retrieval/runner.py with RetrievalPipeline.retrieve().",
            )

    # ── Per-question detail ───────────────────────────────────────────
    rows: list[list[object]] = []
    for record in scored:
        question = by_id[record.id]
        final = record.stages.get("final")
        ranked = final.ranked_ids if final else []
        rank = first_relevant_rank(ranked, question.relevant_ids)
        rows.append(
            [
                record.id,
                question.name or "-",
                "media" if question.modalities & FIGURE_MODALITIES else "text",
                len(question.relevant_ids),
                rank if rank else "MISS",
                _fmt(recall_at_k(ranked, question.relevant_ids, 5)),
                _fmt(ndcg_at_k(ranked, question.graded, NDCG_DEPTH)),
            ]
        )
    report.tables.append(
        table(
            "Per-question results (final ordering)",
            ["Id", "Name", "Answer modality", "Relevant", "First hit rank", "R@5", "nDCG@10"],
            rows,
        )
    )


def _fmt(value: float | None, digits: int = 3) -> str:
    return "-" if value is None else f"{value:.{digits}f}"
