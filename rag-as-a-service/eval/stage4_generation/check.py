"""Stage 4 — Answer quality (RAG-Check's CS / response-generation-hallucination).

Runs the REAL pipeline per gold case — retrieve, then generate — and scores the
answer three ways, cheapest first:

  1. deterministic.py  citations, numeric recall, abstention, span partition
  2. judge.py          per-span support, answer correctness, answer relevance
  3. (RAGAS)           left to the existing RAGEvaluator / /evaluate endpoint,
                       which this deliberately does not reimplement

The headline difference from scripts/eval_llm_judge.py: correctness is measured
PER OBJECTIVE SPAN, not once per answer. eval_runs.md case 4 ("1/2 — generator
omitted audited financials + KYC/AML") is the failure that whole-answer scoring
cannot localise; span scoring names the missing clause.
"""

import asyncio
import sys

sys.path.insert(0, "src")

from gernas_rag.config.settings import get_settings  # noqa: E402
from gernas_rag.generation.generator import ResponseGenerator  # noqa: E402
from gernas_rag.llm.factory import get_llm  # noqa: E402
from gernas_rag.models.retrieval import RetrieveRequest  # noqa: E402

from ..common.gold import load_gold  # noqa: E402
from ..core import report  # noqa: E402
from ..core.cases import CaseStore  # noqa: E402
from ..stage3_retrieval.check import build_pipeline  # noqa: E402
from . import deterministic, judge  # noqa: E402


def _format_context(chunks, images) -> str:
    parts = [f"[{i}] Source: {c.source}\n{(c.text or '')[:1500]}" for i, c in enumerate(chunks, 1)]
    parts += [
        f"[I{i}] FIGURE Source: {im.source} p{im.page_number}\nCaption: {im.caption}"
        for i, im in enumerate(images, 1)
    ]
    return "\n\n---\n\n".join(parts) if parts else "(nothing retrieved)"


def _build_generator(settings):
    payload_builder = None
    if settings.llm.vision_enabled and settings.multimodal.enabled:
        from gernas_rag.generation.image_payload import ImagePayloadBuilder
        from gernas_rag.images.store import get_asset_store

        payload_builder = ImagePayloadBuilder(
            get_asset_store(settings.multimodal.storage), settings.llm
        )
    return ResponseGenerator(settings, get_llm(settings.llm), payload_builder)


def run(
    limit: int | None = None,
    only: str | None = None,
    top_k: int | None = None,
    delay: float = 1.0,
    judge_model: str | None = None,
    fresh: bool = False,
):
    settings = get_settings()
    cases = load_gold()
    if only:
        wanted = {s.strip() for s in only.split(",")}
        cases = [c for c in cases if c.id in wanted]

    # Checkpoint: ~3 API calls per case means a free-tier quota wall is a
    # question of when, not if. Completed cases are skipped so a rerun resumes
    # rather than re-paying, and metrics below are computed from every case
    # ever recorded — not just this invocation's.
    store = CaseStore("stage4")
    if fresh:
        store.clear()
    already = store.done_keys()
    pending = [c for c in cases if c.id not in already]
    skipped = len(cases) - len(pending)

    # --limit is applied AFTER the checkpoint filter, so it means "N MORE cases
    # this run". Applying it first would re-request the same N every time and
    # never advance past them — which defeats the whole point of resuming.
    if limit:
        pending = pending[:limit]

    k = top_k or settings.retrieval.final_top_k
    result = report.StageResult(
        stage="stage4",
        title="Stage 4 — Answer Quality",
        context={
            "generator_text": settings.llm.model_name,
            "generator_vision": (
                settings.llm.vision_model_name if settings.llm.vision_enabled else "off"
            ),
            "final_top_k": k,
            "cases": len(cases),
        },
    )

    try:
        judge_llm, judge_name = judge.build_judge(settings, judge_model)
    except judge.JudgeCollisionError as exc:
        # Hard stop, not a degraded run: scores from a self-grading judge look
        # fine and mean nothing, which is worse than no scores at all.
        result.notes.append(f"REFUSED TO RUN: {exc}")
        for name in (
            "answer_correctness", "span_correctness", "groundedness",
            "answer_relevancy", "citation_validity", "citation_support",
            "numeric_recall", "abstention_accuracy",
        ):
            result.add(name, report.NA, "judge collision — see notes")
        return result

    result.context["judge"] = judge_name

    pipeline = build_pipeline(settings)
    generator = _build_generator(settings)

    async def _one(case) -> dict:
        """Score a single case. Raw values only — display formatting happens at
        report time, so a checkpointed row stays re-aggregatable."""
        response = await pipeline.retrieve(
            RetrieveRequest(query=case.question, top_k=k, include_images=True)
        )
        chunks, images = response.chunks, response.images
        answer = await generator.generate(case.question, chunks, images)

        det = deterministic.score(
            answer, case.expected_answer, len(chunks), len(images), case.answerable
        )
        row = {
            "id": case.id,
            "name": case.name[:30],
            "answerable": case.answerable,
            "n_spans": len(det.spans),
            "n_subjective": sum(1 for s in det.spans if s.subjective),
            "cite_valid": det.citations.valid,
            "cite_total": det.citations.total,
            "citation_validity": det.citations.validity,
            "numeric": det.numeric,
            "subjective_ratio": det.subjective_ratio,
            "abstention_ok": det.abstention_ok,
        }

        if not case.answerable:
            return row  # refusals need no judge call

        context = _format_context(chunks, images)
        span_verdicts = await judge.judge_spans(judge_llm, context, det.spans)
        answer_verdict = await judge.judge_answer(
            judge_llm, case.question, case.expected_answer, answer
        )

        objective = [i for i, s in enumerate(det.spans) if s.objective]
        if objective and "error" not in span_verdicts:
            supported = sum(1 for i in objective if span_verdicts.get(i, {}).get("supported"))
            row["span_supported"] = supported
            row["span_total"] = len(objective)
            row["span_score"] = supported / len(objective)

        if "error" not in answer_verdict:
            grade = answer_verdict.get("answer_correct", 0)
            row["correct"] = grade
            row["relevant"] = bool(answer_verdict.get("answer_relevant"))
            row["why"] = (answer_verdict.get("reason") or "")[:80]
        return row

    async def _drive() -> None:
        for case in pending:
            try:
                row = await _one(case)
            except Exception as exc:  # noqa: BLE001
                # One case must never cost the whole run. On a free tier the
                # usual cause is a quota wall after Groq's 3 retries: the error
                # is checkpointed WITHOUT a score, so a later rerun retries
                # exactly this case and keeps everything already paid for.
                store.append(case.id, {"id": case.id, "name": case.name[:30], "error": str(exc)[:200]})
                print(f"  case {case.id} FAILED: {str(exc)[:120]}")
                if delay:
                    await asyncio.sleep(delay)
                continue

            store.append(case.id, row)  # flushed now, not at the end
            if delay:
                await asyncio.sleep(delay)

    asyncio.run(_drive())

    # Aggregate over EVERY case ever recorded, not just this invocation's —
    # that is what makes `--only 1,2,3` then `--only 4,5,6` accumulate.
    rows = store.rows()
    scored = [r for r in rows if not r.get("error")]
    errored = [r for r in rows if r.get("error")]

    def col(name, rs=None):
        return [r[name] for r in (rs or scored) if r.get(name) is not None]

    def mean(xs) -> float | None:
        return (sum(xs) / len(xs)) if xs else report.NA

    answerable = [r for r in scored if r.get("answerable")]
    correctness = [1.0 if r.get("correct") == 2 else 0.0 for r in answerable if "correct" in r]
    relevancy = [1.0 if r.get("relevant") else 0.0 for r in answerable if "relevant" in r]
    span_scores = col("span_score", answerable)
    abstentions = [r["abstention_ok"] for r in scored if r.get("abstention_ok") is not None]

    result.rows = [
        {
            "id": r["id"], "name": r.get("name", ""),
            "spans": f"{r.get('n_spans','?')}({r.get('n_subjective',0)}subj)",
            "cites": f"{r.get('cite_valid','?')}/{r.get('cite_total','?')}",
            "supported": (
                f"{r['span_supported']}/{r['span_total']}" if "span_total" in r else "n/a"
            ),
            "correct": r.get("correct", "—"),
            "numeric": "—" if r.get("numeric") is None else f"{r['numeric']:.0%}",
            "note": r.get("error") or r.get("why", ""),
        }
        for r in sorted(rows, key=lambda x: int(x["id"]) if str(x["id"]).isdigit() else 0)
    ]

    result.add("answer_correctness", mean(correctness), f"grade==2 over {len(correctness)} cases")
    result.add("span_correctness", mean(span_scores), f"per objective span, {len(span_scores)} cases")
    result.add("groundedness", mean(span_scores), "objective spans supported by context")
    result.add("answer_relevancy", mean(relevancy), "answer addresses the question asked")
    result.add("citation_validity", mean(col("citation_validity")), "[N]/[IN] resolve to retrieved items")
    result.add("citation_support", report.NA, "needs per-citation judging — not yet implemented")
    result.add("numeric_recall", mean(col("numeric")), f"over {len(col('numeric'))} cases with numbers")
    result.add(
        "abstention_accuracy",
        mean([1.0 if a else 0.0 for a in abstentions]),
        f"{sum(abstentions)}/{len(abstentions)} unanswerable cases correctly declined",
    )
    result.add("subjective_span_ratio", mean(col("subjective_ratio")), "share of hedged spans")

    result.context["scored_total"] = f"{len(scored)} case(s) recorded"
    if skipped:
        result.notes.append(
            f"{skipped} case(s) already checkpointed and skipped — they are still "
            f"included in the metrics above. Use --fresh to rescore from scratch."
        )
    if errored:
        result.notes.append(
            f"{len(errored)} case(s) errored and were NOT scored: "
            f"{', '.join(r['id'] for r in errored[:10])}. Re-run the same command "
            "to retry only those — completed cases will be skipped."
        )

    if not abstentions:
        result.notes.append(
            "No unanswerable cases in this selection — abstention_accuracy is n/a. "
            "pipeline_suite.yaml's out-of-corpus cases are the intended source."
        )
    result.notes.append(
        "span_correctness and groundedness are computed over OBJECTIVE spans only; "
        "hedged/subjective statements have no truth value to score (RAG-Check III-A)."
    )
    return result
