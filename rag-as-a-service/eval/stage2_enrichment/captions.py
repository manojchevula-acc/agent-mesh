"""Caption fidelity against human transcriptions.

The single most valuable number in the suite is ``caption_numeric_recall``: the
share of quantities printed on a figure that survive into the caption the system
indexes. Everything downstream inherits a caption error, and no downstream metric
can detect it — an answer grounded in a caption that says 12% where the chart
says 21% is faithful, cited, confident and wrong.

Scoring is deterministic (set comparison of numeric facts, plus character error
rate), so it costs nothing to run, never flakes, and can gate CI.
"""

from __future__ import annotations

from ..core.corpus import ChunkIndex
from ..core.metrics import mean
from ..core.models import FigureTranscription, StageReport, TranscriptionSet
from ..core.numeric import compare_numeric
from ..core.reporting import build_metric, rate, table
from ..core.text import char_error_rate
from ..core.thresholds import STAGE2_CAPTIONS

STAGE = "stage2_captions"


def scaffold(index: ChunkIndex, existing: TranscriptionSet | None) -> TranscriptionSet:
    """Draft one transcription entry per media chunk for a human to fill in.

    The VLM's own caption is copied into ``vlm_caption`` (never into
    ``transcription``) so the reviewer can correct it against the image instead
    of typing from scratch — while the scored field stays empty until a human
    has actually looked at the figure.
    """
    current = existing.by_ref() if existing else {}
    items: list[FigureTranscription] = []
    for chunk in sorted(index.media, key=lambda c: (c.document, c.source_page or 0)):
        if not chunk.artifact_ref:
            continue
        previous = current.get(chunk.artifact_ref)
        if previous is not None and previous.verified:
            items.append(previous)  # Never clobber reviewed ground truth.
            continue
        items.append(
            FigureTranscription(
                artifact_ref=chunk.artifact_ref,
                document=chunk.document,
                page=chunk.source_page,
                modality=chunk.modality,
                transcription=previous.transcription if previous else "",
                vlm_caption=chunk.text,
                verified=False,
                notes=previous.notes
                if previous
                else "Transcribe every number, label and header visible in the image, "
                "then set verified: true. Do not copy vlm_caption — that is what is "
                "being tested.",
            )
        )
    # Keep any entries whose artifact is no longer indexed; they may be orphans
    # under investigation, and silently deleting reviewed work would be worse.
    known = {i.artifact_ref for i in items}
    items.extend(entry for ref, entry in current.items() if ref not in known)
    return TranscriptionSet(items=items)


def score(index: ChunkIndex, transcriptions: TranscriptionSet, report: StageReport) -> None:
    """Score captions against verified transcriptions and report per-figure detail."""
    media = index.media
    verified = transcriptions.verified_by_ref()

    # ── Ground-truth-free checks over every media chunk ────────────────
    empty = [c for c in media if not c.text.strip()]
    illegible = [c for c in media if "[illegible]" in c.text.lower()]
    report.add_metric(
        build_metric(
            STAGE2_CAPTIONS,
            "empty_caption_rate",
            rate(len(empty), len(media)),
            detail=f"{len(empty)}/{len(media)} media chunks have no caption text",
        )
    )
    report.add_metric(
        build_metric(
            STAGE2_CAPTIONS,
            "illegible_marker_rate",
            rate(len(illegible), len(media)),
            detail="captions containing [illegible]; a high rate means images_scale is too low",
        )
    )

    scored = [c for c in media if c.artifact_ref in verified]
    report.add_metric(
        build_metric(
            STAGE2_CAPTIONS,
            "transcription_coverage",
            rate(len(scored), len(media)),
            detail=f"{len(scored)}/{len(media)} media chunks have a verified transcription",
        )
    )
    if not scored:
        report.add_finding(
            "warn",
            "NO_VERIFIED_TRANSCRIPTIONS",
            "corpus",
            "No verified transcriptions, so caption fidelity is unmeasured. Run "
            "`--init` to scaffold data/eval/figure_transcriptions.json, transcribe each "
            "image by hand and set verified: true.",
        )
        for name in ("caption_numeric_recall", "caption_numeric_hallucination_rate", "caption_cer"):
            report.add_metric(build_metric(STAGE2_CAPTIONS, name, None))
        return

    # ── Scored comparison ─────────────────────────────────────────────
    recalls: list[float | None] = []
    hallucinations: list[float | None] = []
    cers: list[float | None] = []
    rows: list[list[object]] = []

    for chunk in sorted(scored, key=lambda c: (c.document, c.source_page or 0)):
        truth = verified[chunk.artifact_ref]
        comparison = compare_numeric(truth.transcription, chunk.text)
        cer = char_error_rate(truth.transcription, chunk.text)

        recalls.append(comparison.recall)
        hallucinations.append(comparison.hallucination_rate)
        cers.append(cer)

        rows.append(
            [
                chunk.document,
                f"p{chunk.source_page}" if chunk.source_page is not None else "-",
                chunk.modality,
                f"{comparison.recall:.2f}" if comparison.recall is not None else "-",
                f"{comparison.hallucination_rate:.2f}"
                if comparison.hallucination_rate is not None
                else "-",
                f"{cer:.3f}" if cer is not None else "-",
                ", ".join(comparison.missing[:6]) or "-",
                ", ".join(comparison.extra[:6]) or "-",
            ]
        )

        if comparison.missing:
            report.add_finding(
                "error",
                "CAPTION_NUMERIC_MISSING",
                chunk.chunk_id,
                f"{chunk.document} p{chunk.source_page}: the caption omits "
                f"{len(comparison.missing)} value(s) printed on the image: "
                f"{', '.join(comparison.missing[:8])}. Any question needing them is "
                "unanswerable from the index even when this chunk is retrieved.",
            )
        if comparison.extra:
            report.add_finding(
                "error",
                "CAPTION_NUMERIC_HALLUCINATED",
                chunk.chunk_id,
                f"{chunk.document} p{chunk.source_page}: the caption contains "
                f"{len(comparison.extra)} value(s) not present in the transcription: "
                f"{', '.join(comparison.extra[:8])}. Answers grounded in this chunk will "
                "be faithful to the caption and still wrong.",
            )

    report.add_metric(
        build_metric(
            STAGE2_CAPTIONS,
            "caption_numeric_recall",
            mean(recalls),
            detail="share of printed quantities that survive into the caption",
        )
    )
    report.add_metric(
        build_metric(
            STAGE2_CAPTIONS,
            "caption_numeric_hallucination_rate",
            mean(hallucinations),
            detail="share of caption quantities absent from the human transcription",
        )
    )
    report.add_metric(
        build_metric(
            STAGE2_CAPTIONS,
            "caption_cer",
            mean(cers),
            detail="character error rate on normalised text",
        )
    )

    report.tables.append(
        table(
            "Per-figure caption fidelity",
            ["Document", "Page", "Modality", "Num recall", "Hallu rate", "CER", "Missing", "Hallucinated"],
            rows,
            note="Missing = printed on the image but absent from the caption. "
            "Hallucinated = in the caption but not on the image.",
        )
    )
    report.meta["figures_scored"] = len(scored)
