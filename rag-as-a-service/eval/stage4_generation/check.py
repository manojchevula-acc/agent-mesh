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
from gernas_rag.models.retrieval import (  # noqa: E402
    RetrievedChunk,
    RetrievedImage,
    RetrieveRequest,
)

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
    reuse_retrieval: bool = False,
    generate_top_k: int | None = None,
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
            "retrieval_top_k": k,
            "generate_top_k": generate_top_k or k,
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

    # ── Optional retrieval reuse ─────────────────────────────────────
    # Loads the context stage 3 already retrieved. Saves ~25s of local model
    # work per case and guarantees both stages scored IDENTICAL context.
    #
    # Refuses on a top_k mismatch rather than silently blending: context
    # retrieved at k=3 scored as though it were k=5 would quietly misattribute
    # a retrieval gap to the generator.
    reused: dict[str, dict] = {}
    if reuse_retrieval:
        rows = CaseStore("stage3_retrieval").rows()
        if not rows:
            raise RuntimeError(
                "--reuse-retrieval needs stage 3's stored context, but none "
                "exists. Run:  python -m eval stage3 --top-k "
                f"{k}   then re-run this command."
            )
        stored_k = sorted({r.get("top_k") for r in rows})
        if any(sk != k for sk in stored_k):
            # Name the concrete fix. `k` defaults to settings.retrieval.final_top_k
            # when --top-k is absent, so the usual cause is a forgotten flag
            # rather than a deliberately different value.
            raise RuntimeError(
                f"--reuse-retrieval: stage 3 stored context at top_k={stored_k[0]}, "
                f"but this run is using top_k={k}"
                + (
                    " (from config, because --top-k was not passed)"
                    if top_k is None
                    else ""
                )
                + f".\n  Fix:  add  --top-k {stored_k[0]}  to this command"
                + f"\n  or:   re-run  python -m eval stage3 --top-k {k}"
                + "\n  Note: --generate-top-k is separate — it controls how many "
                "chunks reach the model, and does not need to match."
            )
        reused = {r["id"]: r for r in rows}
        missing = [c.id for c in pending if c.id not in reused]
        if missing:
            # Print it NOW, not only in the report. Otherwise the first sign is
            # a "Hybrid search complete" log line minutes into the run, and it
            # looks like --reuse-retrieval was ignored.
            print(
                f"\n  NOTE: {len(missing)} of {len(pending)} case(s) have no stored "
                f"context and will be retrieved live: {', '.join(missing[:10])}"
                f"\n        stage 3 only stored: {', '.join(sorted(reused, key=lambda x: int(x) if x.isdigit() else 0)[:10])}"
                f"\n        To cache every case:  python -m eval stage3 --top-k {k}\n"
            )
            result.notes.append(
                f"{len(missing)} case(s) had no stored context and were "
                f"retrieved fresh: {', '.join(missing[:10])}"
            )
        result.context["retrieval"] = (
            f"reused from stage3 ({len(pending) - len(missing)}/{len(pending)} cases)"
        )

    # Build the retrieval pipeline ONLY if something actually needs retrieving.
    # It loads BGE-M3, SigLIP-2 and the cross-encoder reranker — ~45s and a lot
    # of memory — which is pure waste when every case is served from cache.
    needs_pipeline = any(c.id not in reused for c in pending)
    pipeline = build_pipeline(settings) if needs_pipeline else None
    generator = _build_generator(settings)

    async def _one(case) -> dict:
        """Score a single case. Raw values only — display formatting happens at
        report time, so a checkpointed row stays re-aggregatable."""
        cached = reused.get(case.id)
        if cached is not None:
            chunks = [RetrievedChunk(**c) for c in cached["chunks"]]
            images = [RetrievedImage(**i) for i in cached["images"]]
        else:
            response = await pipeline.retrieve(
                RetrieveRequest(query=case.question, top_k=k, include_images=True)
            )
            chunks, images = response.chunks, response.images

        # Retrieval depth and GENERATION depth are separate concerns. Retrieval
        # is measured deep (did the right chunk come back at all?), while the
        # model may only be handed the top few because of a context or vision
        # token budget. Truncating here — after retrieval, before the prompt —
        # reproduces that production constraint and makes its cost visible:
        # a case that retrieves at rank 3 but is generated with 2 chunks fails
        # for a budget reason, not a retrieval one.
        retrieved_depth = len(chunks)
        if generate_top_k:
            chunks = chunks[:generate_top_k]
        answer = await generator.generate(case.question, chunks, images)

        det = deterministic.score(
            answer, case.expected_answer, len(chunks), len(images), case.answerable
        )
        row = {
            "id": case.id,
            "name": case.name[:30],
            "answerable": case.answerable,
            # The ANSWER and the CONTEXT it was generated from. Two reasons,
            # both learned the hard way on stage 2b:
            #   1. a score says a case failed; only these say why
            #   2. every scorer fix (three so far) otherwise costs a full
            #      re-run at 3 API calls per case
            "question": case.question,
            "gold": case.expected_answer,
            "answer": answer,
            "chunks": [
                {
                    "source": c.source,
                    "content_type": c.content_type,
                    "page": c.page_number,
                    "score": round(c.score, 4),
                    "text": (c.text or "")[:400],
                }
                for c in chunks
            ],
            "images": [
                {
                    "asset_id": im.asset_id,
                    "source": im.source,
                    "page": im.page_number,
                    "caption": (im.caption or "")[:160],
                    "score": round(im.score, 4),
                }
                for im in images
            ],
            "n_spans": len(det.spans),
            "n_subjective": sum(1 for s in det.spans if s.subjective),
            "retrieved": retrieved_depth,
            "generated_with": len(chunks),
            # A gold-relevant chunk that was retrieved but truncated away is
            # the context budget's fault, not retrieval's or the model's. Flag
            # it so the report can separate the two.
            "truncated": retrieved_depth - len(chunks),
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

    # Detail for anything that did not cleanly pass, so the report answers
    # "why did this fail" without a second run or a JSON dig.
    def _failed(r: dict) -> bool:
        if r.get("error"):
            return True
        if not r.get("answerable"):
            return not r.get("abstention_ok")
        if r.get("correct") is not None and r["correct"] != 2:
            return True
        return bool(r.get("span_total")) and r.get("span_supported", 0) < r["span_total"]

    for r in sorted(rows, key=lambda x: int(x["id"]) if str(x["id"]).isdigit() else 0):
        if not _failed(r):
            continue
        verdict = (
            "ERROR" if r.get("error")
            else "FABRICATED" if not r.get("answerable")
            else f"correct={r.get('correct')}  spans={r.get('span_supported')}/{r.get('span_total')}"
        )
        result.details += [
            f"### `{r['id']}` {r.get('name','')} — {verdict}", "",
            f"**Question:** {r.get('question','')}", "",
            f"**Gold answer:** {r.get('gold') or '_(unanswerable — should decline)_'}", "",
            f"**Generated:**\n\n```\n{(r.get('answer') or r.get('error') or '')[:1500]}\n```", "",
            f"**Judge:** {r.get('why','—')}", "",
            f"**Context** (retrieved {r.get('retrieved','?')}, "
            f"passed {r.get('generated_with','?')} to the model):", "",
            "| # | type | source | p | score | text |", "|---|---|---|---|---|---|",
        ]
        for n, c in enumerate(r.get("chunks") or [], 1):
            txt = (c.get("text") or "").replace("\n", " ").replace("|", "\\|")[:110]
            result.details.append(
                f"| {n} | `{c.get('content_type')}` | {c.get('source','')[:26]} | "
                f"{c.get('page')} | {c.get('score')} | {txt} |"
            )
        for n, im in enumerate(r.get("images") or [], 1):
            cap = (im.get("caption") or "").replace("\n", " ").replace("|", "\\|")[:110]
            result.details.append(
                f"| I{n} | `image` | {im.get('source','')[:26]} | {im.get('page')} | "
                f"{im.get('score')} | {cap} |"
            )
        result.details.append("")

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
