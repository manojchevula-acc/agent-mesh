"""Caption fidelity against human transcriptions.

The single most valuable number in the suite is ``caption_numeric_recall``: the
share of quantities printed on a figure that survive into the caption the system
indexes. Everything downstream inherits a caption error, and no downstream metric
can detect it — an answer grounded in a caption that says 12% where the chart
says 21% is faithful, cited, confident and wrong.

Scoring is deterministic by default (set comparison of numeric facts, plus
character error rate), so it costs nothing to run, never flakes, and can gate
CI. ``compare_numeric`` is also format-blind, though: it treats any bare digit
as a fact, so a markdown numbered list ("1. Item A") or a spelled-out ordinal
("Fourth Level" vs "4") produces a missing/hallucinated finding that has nothing
to do with caption accuracy. When an LLM is configured (``settings.llm``, same
provider/key ingestion and stage 4 already use), ``score`` re-triages only the
specific candidates the deterministic parser flagged — never proposes new ones
— against the full transcription and caption text, asking it to tell a
formatting artifact apart from a genuine numeric error. When no LLM is
configured, or a judge call fails, this falls back to the raw deterministic
result unchanged — a judge outage can only make this stage stricter, never
silently hide a real caption defect.

The LLM step is deliberately scoped to adjudicating already-flagged candidates,
not to a free-form "does this caption look right" judgment — that broader
question is exactly what a judge routinely waves through a transposed digit on
(see stage4_generation/scoring.py's docstring for why stage 4 keeps its numeric
check deterministic-first too).
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from gernas_rag.llm.base import BaseLLM, Message

from ..core.corpus import ChunkIndex
from ..core.metrics import mean
from ..core.models import FigureTranscription, StageReport, TranscriptionSet
from ..core.numeric import compare_numeric
from ..core.reporting import build_metric, rate, table
from ..core.text import char_error_rate
from ..core.thresholds import STAGE2_CAPTIONS

STAGE = "stage2_captions"

# ══════════════════════════════════════════════════════════════════════
# LLM re-triage of deterministic missing/hallucinated candidates
# ══════════════════════════════════════════════════════════════════════

_JUDGE_SYSTEM_PROMPT = (
    "You are checking a deterministic numeric-fact comparison between a human "
    "TRANSCRIPTION of a figure/table image (ground truth) and a VLM-generated "
    "CAPTION of the same image, which is what a RAG system actually indexes.\n\n"
    "A naive parser has already flagged some numbers as mismatched, but it treats "
    "every bare digit as a fact, so it cannot tell a genuine numeric error apart "
    "from a formatting artifact — a markdown list marker ('1.', '2.'), a "
    "spelled-out number ('Fourth' vs '4'), or a differently-phrased but "
    "equivalent label.\n\n"
    "For each numbered candidate below, read both full texts and decide:\n"
    "- FALSE_POSITIVE: the candidate is a list marker, spelled-out ordinal, or "
    "other artifact — not a real transcribed data value in dispute.\n"
    "- REAL: the candidate is a genuine data value (a measurement, date, rate, "
    "amount, tick value, etc.) that is actually missing from the caption or "
    "wrongly added to it.\n\n"
    "Be conservative: if there is any real chance the value is a genuine data "
    "point in dispute, classify it REAL. Only mark FALSE_POSITIVE when you are "
    "confident it is a parsing artifact, not a content error.\n\n"
    "Respond with EXACTLY one line per candidate, in order, nothing else:\n"
    "N: REAL|FALSE_POSITIVE"
)

_VERDICT_RE = re.compile(r"^\s*(\d+)\s*:\s*(REAL|FALSE_POSITIVE)", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class TriageResult:
    real_missing: list[str]
    real_extra: list[str]
    filtered_missing: list[str]  # false positives the LLM cleared out of "missing"
    filtered_extra: list[str]  # false positives the LLM cleared out of "extra"
    judged: bool  # False if the judge was skipped/failed — caller keeps the originals


def _build_triage_prompt(
    transcription: str, caption: str, missing: list[str], extra: list[str]
) -> tuple[str, list[tuple[str, str]]]:
    lines = [
        f"TRANSCRIPTION (ground truth):\n{transcription}",
        f"\nCAPTION (system under test):\n{caption}",
        "\nCandidates:",
    ]
    index_map: list[tuple[str, str]] = []  # (kind, value), aligned to candidate number
    n = 1
    for value in missing:
        lines.append(f"{n}. [flagged missing from caption] {value}")
        index_map.append(("missing", value))
        n += 1
    for value in extra:
        lines.append(f"{n}. [flagged extra in caption, absent from transcription] {value}")
        index_map.append(("extra", value))
        n += 1
    return "\n".join(lines), index_map


class CaptionJudge:
    """Re-triages ``compare_numeric`` candidates through an LLM, bounded concurrency."""

    def __init__(self, llm: BaseLLM, max_concurrent: int = 3, max_attempts: int = 2) -> None:
        self._llm = llm
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_attempts = max_attempts

    async def triage(
        self, transcription: str, caption: str, missing: list[str], extra: list[str]
    ) -> TriageResult:
        if not missing and not extra:
            return TriageResult([], [], [], [], judged=True)

        prompt, index_map = _build_triage_prompt(transcription, caption, missing, extra)
        messages = [
            Message(role="system", content=_JUDGE_SYSTEM_PROMPT),
            Message(role="user", content=prompt),
        ]
        async with self._semaphore:
            for attempt in range(1, self._max_attempts + 1):
                try:
                    raw = await self._llm.generate(messages)
                except Exception:  # noqa: BLE001 — fail open, keep every candidate REAL.
                    if attempt == self._max_attempts:
                        return TriageResult(missing, extra, [], [], judged=False)
                    await asyncio.sleep(1.0 * attempt)
                    continue

                verdicts = {int(m.group(1)): m.group(2).upper() for m in _VERDICT_RE.finditer(raw)}
                if len(verdicts) != len(index_map):
                    # Partial/unparseable response — fail open rather than trust it.
                    if attempt == self._max_attempts:
                        return TriageResult(missing, extra, [], [], judged=False)
                    continue

                real_missing: list[str] = []
                real_extra: list[str] = []
                filtered_missing: list[str] = []
                filtered_extra: list[str] = []
                for i, (kind, value) in enumerate(index_map, start=1):
                    verdict = verdicts.get(i, "REAL")
                    is_missing = kind == "missing"
                    if verdict == "REAL":
                        (real_missing if is_missing else real_extra).append(value)
                    else:
                        (filtered_missing if is_missing else filtered_extra).append(value)
                return TriageResult(real_missing, real_extra, filtered_missing, filtered_extra, judged=True)

        return TriageResult(missing, extra, [], [], judged=False)  # pragma: no cover — defensive


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


async def score(
    index: ChunkIndex,
    transcriptions: TranscriptionSet,
    report: StageReport,
    judge: CaptionJudge | None = None,
) -> None:
    """Score captions against verified transcriptions and report per-figure detail.

    When ``judge`` is given, every figure with a deterministic missing/extra
    candidate is re-triaged through it before scoring or raising a finding —
    see the module docstring for why this is scoped to re-checking flagged
    candidates rather than a free-form judgment. ``judge=None`` (no LLM
    configured, or the caller chose not to build one) runs the original
    deterministic-only comparison unchanged.
    """
    media = index.media
    verified = transcriptions.verified_by_ref()
    report.meta["caption_llm_judge"] = "enabled" if judge is not None else "unavailable (deterministic only)"

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
    llm_filtered_total = 0

    for chunk in sorted(scored, key=lambda c: (c.document, c.source_page or 0)):
        truth = verified[chunk.artifact_ref]
        # Units are compared leniently and label-like integers dropped, because
        # neither divergence says anything about caption accuracy:
        #   * a transcription writes "65/80/100/130 bps", giving the unit to 130
        #     alone, while the caption attaches it to every value — the same
        #     numbers then register as both missing AND hallucinated;
        #   * "Tier 1 / Tier 2 / Tier 3" parses as three quantities.
        # A genuine unit error still fails: unit_compatible only lets a
        # *unit-less* side match, so 50 bps never matches 0.5%.
        comparison = compare_numeric(
            truth.transcription, chunk.text, strict_units=False, drop_weak=True
        )
        cer = char_error_rate(truth.transcription, chunk.text)

        missing, extra = comparison.missing, comparison.extra
        if judge is not None and (missing or extra):
            result = await judge.triage(truth.transcription, chunk.text, missing, extra)
            missing, extra = result.real_missing, result.real_extra
            if result.filtered_missing or result.filtered_extra:
                llm_filtered_total += len(result.filtered_missing) + len(result.filtered_extra)
                report.add_finding(
                    "info",
                    "CAPTION_LLM_FALSE_POSITIVE_FILTERED",
                    chunk.chunk_id,
                    f"{chunk.document} p{chunk.source_page}: LLM re-triage classified "
                    f"{len(result.filtered_missing)} 'missing' and {len(result.filtered_extra)} "
                    "'extra' candidate(s) as formatting artifacts (list markers, spelled-out "
                    f"numbers, etc.), not real discrepancies: "
                    f"{', '.join(result.filtered_missing + result.filtered_extra)[:200]}.",
                )
            if not result.judged:
                report.add_finding(
                    "warn",
                    "CAPTION_LLM_JUDGE_FAILED",
                    chunk.chunk_id,
                    f"{chunk.document} p{chunk.source_page}: LLM re-triage failed or returned "
                    "an unparseable response; falling back to the raw deterministic result for "
                    "this figure.",
                )

        recall = (
            (comparison.reference_count - len(missing)) / comparison.reference_count
            if comparison.reference_count
            else None
        )
        hallucination_rate = (
            len(extra) / comparison.hypothesis_count if comparison.hypothesis_count else None
        )
        recalls.append(recall)
        hallucinations.append(hallucination_rate)
        cers.append(cer)

        rows.append(
            [
                chunk.document,
                f"p{chunk.source_page}" if chunk.source_page is not None else "-",
                chunk.modality,
                f"{recall:.2f}" if recall is not None else "-",
                f"{hallucination_rate:.2f}" if hallucination_rate is not None else "-",
                f"{cer:.3f}" if cer is not None else "-",
                ", ".join(missing[:6]) or "-",
                ", ".join(extra[:6]) or "-",
            ]
        )

        if missing:
            report.add_finding(
                "error",
                "CAPTION_NUMERIC_MISSING",
                chunk.chunk_id,
                f"{chunk.document} p{chunk.source_page}: the caption omits "
                f"{len(missing)} value(s) printed on the image: "
                f"{', '.join(missing[:8])}. Any question needing them is "
                "unanswerable from the index even when this chunk is retrieved.",
            )
        if extra:
            report.add_finding(
                "error",
                "CAPTION_NUMERIC_HALLUCINATED",
                chunk.chunk_id,
                f"{chunk.document} p{chunk.source_page}: the caption contains "
                f"{len(extra)} value(s) not present in the transcription: "
                f"{', '.join(extra[:8])}. Answers grounded in this chunk will "
                "be faithful to the caption and still wrong.",
            )

    if llm_filtered_total:
        report.meta["caption_llm_filtered_candidates"] = llm_filtered_total

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
