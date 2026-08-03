"""Score recorded answers: correctness, grounding, attribution and abstention.

Two design decisions worth knowing:

1. **Grounding is checked against verified transcriptions where they exist.**
   A media context block holds the VLM caption, so checking an answer against it
   only proves the model copied the caption faithfully — a caption that misread
   the chart yields a "grounded", cited, wrong answer. Where stage 2 has a human
   transcription for the same artifact, that is substituted, which turns the
   check into "is this number actually on the image".

2. **Numeric scoring is deterministic and runs before the judge.** Most gold
   answers here are quantities, and a set comparison of numeric facts catches a
   transposed digit that an LLM judge reading for gist routinely waves through.
"""

from __future__ import annotations

import re

from ..core.metrics import mean, percentile
from ..core.models import GenerationRun, StageReport, TranscriptionSet
from ..core.numeric import compare_numeric, numeric_facts, unsupported_facts
from ..core.reporting import build_metric, rate, table
from ..core.text import content_tokens, sentences, strip_citations
from ..core.thresholds import STAGE4
from .judge import UNKNOWN, Judgment

STAGE = "stage4_generation"

# "[1]", "[2][3]", "[1, 2]" — the numbering the generator is prompted to use.
_CITATION_RE = re.compile(r"\[\s*(\d+(?:\s*[,;]\s*\d+)*)\s*\]")

_REFUSAL_PATTERNS = [
    r"could not find",
    r"couldn't find",
    r"does not (?:contain|include|specify|provide|mention)",
    r"do(?:es)? not appear",
    r"no (?:relevant )?(?:information|context|policy|document)",
    r"not (?:available|specified|stated|provided|covered) in the (?:provided )?context",
    r"cannot (?:answer|determine|be answered)",
    r"can't (?:answer|determine)",
    r"unable to (?:answer|determine|locate|find)",
    r"insufficient (?:information|context)",
    r"is not addressed",
]
_REFUSAL_RE = re.compile("|".join(_REFUSAL_PATTERNS), re.IGNORECASE)

# A cited block counts as supporting a sentence when it shares a quantity, or
# enough distinctive words. Deliberately coarse: this measures gross citation
# misattribution, not fine-grained entailment, and is reported as non-gating.
_MIN_SHARED_TOKENS = 3


def citations_in(text: str) -> list[int]:
    """Every context index referenced by a ``[N]`` marker, in order of appearance."""
    found: list[int] = []
    for match in _CITATION_RE.finditer(text):
        for part in re.split(r"[,;]", match.group(1)):
            part = part.strip()
            if part.isdigit():
                found.append(int(part))
    return found


def looks_like_refusal(text: str) -> bool:
    return bool(_REFUSAL_RE.search(text or ""))


def _grounding_sources(record, verified: dict[str, str]) -> list[str]:
    """Context texts, with human transcriptions substituted for media captions."""
    sources: list[str] = []
    for context in record.contexts:
        if context.artifact_ref and context.artifact_ref in verified:
            sources.append(verified[context.artifact_ref])
        else:
            sources.append(context.text)
        if context.parent_text:
            sources.append(context.parent_text)
    return sources


def _citation_support(answer: str, record) -> tuple[int, int, int, int]:
    """(cited, valid, supported, sentences_with_citations) for one answer."""
    by_index = {c.index: c for c in record.contexts}
    cited = valid = supported = with_citations = 0

    for sentence in sentences(answer):
        indices = citations_in(sentence)
        if not indices:
            continue
        with_citations += 1
        claim_numbers = {f.key for f in numeric_facts(sentence)}
        claim_tokens = content_tokens(strip_citations(sentence))
        sentence_supported = False
        for index in indices:
            cited += 1
            context = by_index.get(index)
            if context is None:
                continue
            valid += 1
            block_text = f"{context.text}\n{context.parent_text or ''}"
            block_numbers = {f.key for f in numeric_facts(block_text)}
            if claim_numbers and claim_numbers & block_numbers:
                sentence_supported = True
            elif len(claim_tokens & content_tokens(block_text)) >= _MIN_SHARED_TOKENS:
                sentence_supported = True
        if sentence_supported:
            supported += 1
    return cited, valid, supported, with_citations


def score(
    run: GenerationRun,
    gold: list[dict],
    judgments: dict[str, Judgment],
    transcriptions: TranscriptionSet | None,
    report: StageReport,
) -> None:
    gold_by_id = {str(item["id"]): item for item in gold}
    verified = (
        {ref: item.transcription for ref, item in transcriptions.verified_by_ref().items()}
        if transcriptions
        else {}
    )
    records = [r for r in run.records if r.id in gold_by_id]

    report.meta.update(
        {
            "answers_scored": len(records),
            "verified_transcriptions_applied": len(verified),
            "judgments_available": len(judgments),
        }
    )
    if not records:
        report.add_finding(
            "error", "NO_ANSWERS", "run", "The run file contains no answers matching the gold set."
        )
        return

    answerable = [r for r in records if gold_by_id[r.id].get("answerable", True)]
    unanswerable = [r for r in records if not gold_by_id[r.id].get("answerable", True)]

    numeric_recalls: list[float | None] = []
    grounded_rates: list[float | None] = []
    cited_total = valid_total = supported_total = sentences_total = 0
    answers_with_citations = 0
    rows: list[list[object]] = []

    for record in answerable:
        item = gold_by_id[record.id]
        expected = item.get("expected_answer", "")
        answer = record.answer

        comparison = compare_numeric(expected, answer)
        numeric_recalls.append(comparison.recall)

        sources = _grounding_sources(record, verified)
        unsupported, total_facts = unsupported_facts(answer, sources)
        grounded = 1.0 - (len(unsupported) / total_facts) if total_facts else None
        grounded_rates.append(grounded)

        cited, valid, supported, with_citations = _citation_support(answer, record)
        cited_total += cited
        valid_total += valid
        supported_total += supported
        sentences_total += with_citations
        if citations_in(answer):
            answers_with_citations += 1

        judgment = judgments.get(record.id)
        verdict = judgment.verdict if judgment else "-"

        rows.append(
            [
                record.id,
                item.get("name", "-"),
                verdict,
                f"{comparison.recall:.2f}" if comparison.recall is not None else "-",
                f"{grounded:.2f}" if grounded is not None else "-",
                "yes" if record.vision_used else "no",
                ", ".join(comparison.missing[:5]) or "-",
                ", ".join(unsupported[:5]) or "-",
            ]
        )

        if comparison.missing:
            report.add_finding(
                "warn",
                "ANSWER_NUMERIC_MISSING",
                record.id,
                f"The answer omits {len(comparison.missing)} quantity/quantities the gold "
                f"answer states: {', '.join(comparison.missing[:8])}.",
            )
        if unsupported:
            report.add_finding(
                "error",
                "ANSWER_NUMERIC_UNGROUNDED",
                record.id,
                f"{len(unsupported)} quantity/quantities in the answer appear in no retrieved "
                f"context: {', '.join(unsupported[:8])}. "
                + (
                    "Grounding was checked against verified image transcriptions, so this "
                    "is a fabricated or mis-transcribed value."
                    if verified
                    else "No verified transcriptions were available, so a media caption error "
                    "would be invisible here — see stage 2b."
                ),
            )
        for index in citations_in(record.answer):
            if index < 1 or index > len(record.contexts):
                report.add_finding(
                    "error",
                    "CITATION_OUT_OF_RANGE",
                    record.id,
                    f"Answer cites [{index}] but only {len(record.contexts)} context blocks "
                    "were supplied.",
                )

    # ── Correctness & grounding ───────────────────────────────────────
    report.add_metric(
        build_metric(
            STAGE4,
            "answer_numeric_recall",
            mean(numeric_recalls),
            detail="share of gold-answer quantities restated by the agent",
        )
    )
    report.add_metric(
        build_metric(
            STAGE4,
            "answer_numeric_groundedness",
            mean(grounded_rates),
            detail=(
                "share of answer quantities traceable to a retrieved block "
                + (
                    "(verified transcriptions substituted for media captions)"
                    if verified
                    else "(captions used as-is — a caption error is invisible here)"
                )
            ),
        )
    )
    if not verified:
        report.add_finding(
            "warn",
            "NO_TRANSCRIPTION_SUBSTITUTION",
            "stage2b",
            "No verified figure transcriptions were available, so grounding for media-backed "
            "answers was checked against the VLM caption. An answer faithful to a wrong "
            "caption scores as fully grounded. Complete stage 2b to close this gap.",
        )

    # ── Attribution ───────────────────────────────────────────────────
    report.add_metric(
        build_metric(
            STAGE4,
            "citation_presence_rate",
            rate(answers_with_citations, len(answerable)),
            detail="answers containing at least one [N] citation",
        )
    )
    report.add_metric(
        build_metric(
            STAGE4,
            "citation_validity_rate",
            rate(valid_total, cited_total),
            detail=f"{valid_total}/{cited_total} citations point at an existing context block",
        )
    )
    report.add_metric(
        build_metric(
            STAGE4,
            "citation_support_rate",
            rate(supported_total, sentences_total),
            detail="cited sentences sharing a quantity or distinctive wording with the cited "
            "block (coarse heuristic, not entailment)",
        )
    )

    # ── Abstention ────────────────────────────────────────────────────
    if unanswerable:
        refused = sum(1 for r in unanswerable if looks_like_refusal(r.answer))
        report.add_metric(
            build_metric(
                STAGE4,
                "abstention_accuracy",
                rate(refused, len(unanswerable)),
                detail=f"{refused}/{len(unanswerable)} unanswerable questions correctly refused",
            )
        )
        for record in unanswerable:
            if not looks_like_refusal(record.answer):
                report.add_finding(
                    "error",
                    "HALLUCINATED_ON_UNANSWERABLE",
                    record.id,
                    "The corpus cannot support this question, but the system answered it "
                    "instead of refusing.",
                )
        rows.extend(
            [
                record.id,
                gold_by_id[record.id].get("name", "-"),
                "REFUSED" if looks_like_refusal(record.answer) else "ANSWERED",
                "n/a",
                "n/a",
                "yes" if record.vision_used else "no",
                "-",
                "-",
            ]
            for record in unanswerable
        )
    else:
        report.add_metric(build_metric(STAGE4, "abstention_accuracy", None))
        report.add_finding(
            "warn",
            "NO_UNANSWERABLE_QUESTIONS",
            "gold set",
            "Every gold question is answerable, so the system's willingness to invent an "
            "answer is unmeasured. Add questions with \"answerable\": false covering facts "
            "the corpus does not contain.",
        )

    over_abstained = [r for r in answerable if looks_like_refusal(r.answer)]
    if over_abstained:
        report.meta["over_abstentions"] = f"{len(over_abstained)}/{len(answerable)}"
        for record in over_abstained:
            report.add_finding(
                "warn",
                "REFUSED_ANSWERABLE",
                record.id,
                "The system refused a question the gold set says is answerable — usually a "
                "retrieval miss rather than a generation fault (check stage 3 for this id).",
            )

    # ── Judge ─────────────────────────────────────────────────────────
    judged = [judgments[r.id] for r in answerable if r.id in judgments]
    if judged:
        unknown = sum(1 for j in judged if j.verdict == UNKNOWN)
        strict = sum(1 for j in judged if j.is_pass_strict)
        lenient = sum(1 for j in judged if j.is_pass_lenient)
        report.add_metric(
            build_metric(
                STAGE4,
                "judge_strict_pass_rate",
                rate(strict, len(judged)),
                detail=f"{strict}/{len(judged)} graded CORRECT",
            )
        )
        report.add_metric(
            build_metric(
                STAGE4,
                "judge_lenient_pass_rate",
                rate(lenient, len(judged)),
                detail=f"{lenient}/{len(judged)} graded CORRECT or PARTIALLY_CORRECT",
            )
        )
        report.add_metric(
            build_metric(
                STAGE4,
                "judge_unknown_rate",
                rate(unknown, len(judged)),
                detail="judge responses that could not be parsed — missing data, not a verdict",
            )
        )
        for judgment in judged:
            if judgment.verdict == "INCORRECT":
                report.add_finding(
                    "error", "JUDGE_INCORRECT", judgment.question_id, judgment.reason
                )
            elif judgment.verdict == "PARTIALLY_CORRECT":
                report.add_finding(
                    "warn", "JUDGE_PARTIAL", judgment.question_id, judgment.reason
                )
    else:
        for name in ("judge_strict_pass_rate", "judge_lenient_pass_rate", "judge_unknown_rate"):
            report.add_metric(build_metric(STAGE4, name, None))
        report.add_finding(
            "warn",
            "NO_JUDGMENTS",
            "run",
            "No LLM judgments were available; correctness rests on numeric matching alone. "
            "Run with --judge to grade the recorded answers.",
        )

    # ── Multimodal path & latency ─────────────────────────────────────
    eligible = sum(r.hydration_eligible for r in records)
    hydrated = sum(r.images_hydrated for r in records)
    vision_answers = sum(1 for r in records if r.vision_used)
    report.meta.update(
        {
            "hydration_eligible_chunks": eligible,
            "images_hydrated": hydrated,
            "answers_via_vision_llm": f"{vision_answers}/{len(records)}",
        }
    )
    if run.config.get("hydration_enabled") and vision_answers == 0:
        report.add_finding(
            "warn",
            "HYDRATION_NEVER_RAN",
            "config",
            "Hydration is enabled but no answer went through the vision LLM. Either no "
            "media chunk was retrieved, or artifacts failed to resolve (see stage 2a).",
        )

    latencies = [r.retrieval_latency_ms + r.generation_latency_ms for r in records]
    report.tables.append(
        table(
            "Latency (ms)",
            ["Measure", "Retrieval", "Generation", "Total"],
            [
                [
                    "p50",
                    _ms(percentile([r.retrieval_latency_ms for r in records], 50)),
                    _ms(percentile([r.generation_latency_ms for r in records], 50)),
                    _ms(percentile(latencies, 50)),
                ],
                [
                    "p95",
                    _ms(percentile([r.retrieval_latency_ms for r in records], 95)),
                    _ms(percentile([r.generation_latency_ms for r in records], 95)),
                    _ms(percentile(latencies, 95)),
                ],
            ],
        )
    )

    report.tables.append(
        table(
            "Per-question results",
            [
                "Id",
                "Name",
                "Judge",
                "Num recall",
                "Grounded",
                "Vision",
                "Missing quantities",
                "Ungrounded quantities",
            ],
            rows,
        )
    )


def _ms(value: float | None) -> str:
    return "-" if value is None else f"{value:.0f}"


def compare_runs(baseline: GenerationRun, candidate: GenerationRun, report: StageReport) -> None:
    """A/B two generation runs — the hydration-on vs hydration-off question."""
    base_by_id = baseline.by_id()
    rows: list[list[object]] = []
    changed = 0
    for record in candidate.records:
        other = base_by_id.get(record.id)
        if other is None:
            continue
        differs = record.answer.strip() != other.answer.strip()
        changed += differs
        rows.append(
            [
                record.id,
                "yes" if other.vision_used else "no",
                "yes" if record.vision_used else "no",
                "changed" if differs else "same",
                f"{other.generation_latency_ms:.0f} -> {record.generation_latency_ms:.0f}",
            ]
        )
    report.tables.append(
        table(
            "Run comparison",
            ["Id", "Baseline vision", "Candidate vision", "Answer", "Generation ms"],
            rows,
            note=f"{changed}/{len(rows)} answers changed between the two configurations.",
        )
    )
