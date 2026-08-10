"""LLM-as-judge evaluation against tests/fixtures/gold_qa.json.

For each gold case: run the REAL pipeline (retrieve + generate), then ask an
independent judge model to compare what came out against the gold answer.

The judge returns four things, and their combination is the point:

    context_sufficient   did the RETRIEVED CHUNKS contain the answer?
    answer_correct       does the GENERATED ANSWER match the gold answer?
    grounded             is the answer supported by the chunks (no invention)?
    correct_refusal      for unanswerable cases, did it properly decline?

Which yields a diagnosis that tells you WHERE to fix:

    context_sufficient = false                  -> RETRIEVAL failure
    sufficient but answer_correct = 0           -> GENERATION failure
    grounded = false                            -> HALLUCINATION
    both true                                   -> pass

That split is what a single aggregate score (RAGAS or otherwise) cannot give
you: it says whether to go and fix the chunker or the prompt.

JUDGE INDEPENDENCE (R11): the judge must not be either generator. This pipeline
generates with llm.model_name (text) and llm.vision_model_name (vision), so the
judge defaults to a third model and refuses to run if it collides.

Usage:
    python scripts/eval_llm_judge.py --limit 5          # try a few first
    python scripts/eval_llm_judge.py --report
    python scripts/eval_llm_judge.py --only 5,9,10,12
    python scripts/eval_llm_judge.py --no-generate      # judge retrieval only
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "src")

from gernas_rag.config.settings import get_settings  # noqa: E402
from gernas_rag.embeddings.factory import get_embedder  # noqa: E402
from gernas_rag.generation.generator import ResponseGenerator  # noqa: E402
from gernas_rag.llm.base import Message  # noqa: E402
from gernas_rag.llm.factory import get_llm  # noqa: E402
from gernas_rag.models.retrieval import RetrieveRequest  # noqa: E402
from gernas_rag.retrieval.multimodal_pipeline import (  # noqa: E402
    MultimodalRetrievalPipeline,
)
from gernas_rag.retrieval.pipeline import RetrievalPipeline  # noqa: E402
from gernas_rag.vectordb.factory import get_vectordb  # noqa: E402

GOLD = Path("tests/fixtures/gold_qa.json")
DEFAULT_JUDGE = "llama-3.3-70b-versatile"

_JUDGE_SYSTEM = (
    "You are a strict evaluator of a retrieval-augmented generation system for "
    "banking policy documents. You are given a QUESTION, the GOLD ANSWER "
    "(authoritative), the CONTEXT CHUNKS the system retrieved, and the ANSWER "
    "the system generated.\n\n"
    "Judge only what is in front of you. Do not use outside knowledge about "
    "banking. A number that differs from the gold answer is WRONG, not "
    "'approximately right'.\n\n"
    "Return ONLY a JSON object with these keys:\n"
    '  "context_sufficient": true|false  — do the CONTEXT CHUNKS alone contain '
    "enough information to produce the gold answer? Judge the chunks, not the "
    "generated answer.\n"
    '  "answer_correct": 0|1|2  — 2 = matches the gold answer on every material '
    "fact; 1 = partially correct or missing material facts; 0 = wrong, or "
    "declined when the gold answer exists.\n"
    '  "grounded": true|false  — is every factual claim in the ANSWER supported '
    "by the CONTEXT CHUNKS? false if it asserts specifics absent from the "
    "context (this is hallucination, and it is false even if the claim happens "
    "to match the gold answer).\n"
    '  "missing_facts": [ ... ]  — gold facts absent from the generated answer\n'
    '  "reason": "one sentence"\n'
)

_REFUSAL_SYSTEM = (
    "You are evaluating whether a RAG system correctly DECLINED to answer a "
    "question that its document corpus cannot answer. The correct behaviour is "
    "to say it cannot find the information. Inventing a plausible answer, or "
    "answering from loosely-related material, is a FAILURE.\n\n"
    "Return ONLY a JSON object:\n"
    '  "correct_refusal": true|false — did it clearly decline / say it could '
    "not find the information?\n"
    '  "fabricated": true|false — did it assert specifics as if answering?\n'
    '  "reason": "one sentence"\n'
)


def _fmt_chunks(chunks, images) -> str:
    parts = []
    for i, c in enumerate(chunks, 1):
        tag = f" [{c.content_type}]" if c.content_type != "text" else ""
        parts.append(f"[{i}]{tag} Source: {c.source}\n{c.text[:1500]}")
    for i, im in enumerate(images, 1):
        parts.append(
            f"[I{i}] FIGURE (image sent to the model, not text) "
            f"Source: {im.source} p{im.page_number}\nCaption: {im.caption}"
        )
    return "\n\n---\n\n".join(parts) if parts else "(nothing retrieved)"


async def _judge(llm, system: str, payload: str) -> dict:
    raw = await llm.generate([
        Message(role="system", content=system),
        Message(role="user", content=payload),
    ])
    text = raw.strip()
    if "```" in text:  # strip markdown fences if the model adds them
        text = text.split("```")[1].lstrip("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return {"error": "judge returned no JSON", "raw": raw[:300]}
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        return {"error": f"bad JSON: {exc}", "raw": raw[:300]}


def _diagnose(v: dict) -> str:
    if v.get("error"):
        return "JUDGE ERROR"
    if not v.get("context_sufficient"):
        return "RETRIEVAL"
    if v.get("answer_correct", 0) == 0:
        return "GENERATION"
    if not v.get("grounded", True):
        return "HALLUCINATION"
    if v.get("answer_correct") == 1:
        return "PARTIAL"
    return "pass"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="only the first N cases")
    ap.add_argument("--only", help="comma-separated case ids")
    ap.add_argument("--judge-model", default=None)
    ap.add_argument("--no-generate", action="store_true",
                    help="skip generation; judge retrieval sufficiency only")
    ap.add_argument("--report", nargs="?", const="llm_judge_report.md")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="seconds between cases (free-tier rate limits)")
    args = ap.parse_args()

    settings = get_settings()
    cases = json.loads(GOLD.read_text(encoding="utf-8"))
    if args.only:
        want = {s.strip() for s in args.only.split(",")}
        cases = [c for c in cases if c["id"] in want]
    if args.limit:
        cases = cases[: args.limit]

    # ── Judge independence (R11) ─────────────────────────────────────
    judge_model = args.judge_model or settings.evaluation.judge_model
    generators = {settings.llm.model_name}
    if settings.llm.vision_enabled:
        generators.add(settings.llm.vision_model_name)
    if judge_model in generators:
        print(
            f"REFUSING TO RUN: judge model '{judge_model}' is also a GENERATOR "
            f"({sorted(generators)}).\n"
            "A model grading its own output inflates every score with no visible\n"
            f"failure. Use --judge-model {DEFAULT_JUDGE}, or set\n"
            "RAG__EVALUATION__JUDGE_MODEL to a third model."
        )
        sys.exit(2)

    try:
        vectordb = get_vectordb(settings.vectordb)
    except RuntimeError as exc:
        print(f"\n{exc}\n")
        sys.exit(2)

    embedder = get_embedder(settings.embedding)
    mm_embedder = image_store = None
    floor = 0.10
    if settings.multimodal.enabled:
        from gernas_rag.embeddings.multimodal.factory import (
            get_multimodal_embedder,
            registry_score_floor,
        )
        from gernas_rag.vectordb.image_factory import get_image_store

        mm_embedder = get_multimodal_embedder(settings.multimodal.embedding)
        floor = registry_score_floor(settings.multimodal.embedding)
        space = mm_embedder.space
        coll = settings.multimodal.image_collection_name or space.collection_name(
            settings.multimodal.image_collection_base
        )
        image_store = get_image_store(settings.vectordb, coll, vectordb)

    pipeline = MultimodalRetrievalPipeline(
        settings, RetrievalPipeline(settings, embedder, vectordb),
        mm_embedder, image_store, floor,
    )

    payload_builder = None
    if settings.llm.vision_enabled and settings.multimodal.enabled:
        from gernas_rag.generation.image_payload import ImagePayloadBuilder
        from gernas_rag.images.store import get_asset_store

        payload_builder = ImagePayloadBuilder(
            get_asset_store(settings.multimodal.storage), settings.llm
        )
    generator = ResponseGenerator(settings, get_llm(settings.llm), payload_builder)

    # Judge runs on its own LLM instance with the judge model.
    judge_cfg = settings.llm.model_copy(
        update={"model_name": judge_model, "vision_enabled": False}
    )
    judge_llm = get_llm(judge_cfg)

    print(f"generator(text)  : {settings.llm.model_name}")
    print(f"generator(vision): "
          f"{settings.llm.vision_model_name if settings.llm.vision_enabled else '(off)'}")
    print(f"judge            : {judge_model}")
    print(f"cases            : {len(cases)}\n")
    print(f"{'id':>3} {'name':<34} {'suff':>5} {'corr':>5} {'grnd':>5}  diagnosis")
    print("-" * 92)

    records = []
    for case in cases:
        answerable = case.get("answerable", True)
        response = await pipeline.retrieve(
            RetrieveRequest(query=case["question"], top_k=settings.retrieval.final_top_k, include_images=True)
        )
        answer = ""
        if not args.no_generate:
            answer = await generator.generate(
                case["question"], response.chunks, response.images
            )

        context = _fmt_chunks(response.chunks, response.images)

        if answerable:
            verdict = await _judge(
                judge_llm, _JUDGE_SYSTEM,
                f"QUESTION:\n{case['question']}\n\n"
                f"GOLD ANSWER:\n{case['expected_answer']}\n\n"
                f"CONTEXT CHUNKS:\n{context}\n\n"
                f"GENERATED ANSWER:\n{answer or '(generation skipped)'}",
            )
            diag = _diagnose(verdict)
            print(f"{case['id']:>3} {case['name'][:34]:<34} "
                  f"{str(verdict.get('context_sufficient', '?'))[:5]:>5} "
                  f"{str(verdict.get('answer_correct', '?')):>5} "
                  f"{str(verdict.get('grounded', '?'))[:5]:>5}  {diag}"
                  f"{'  ' + verdict.get('reason', '')[:44] if diag != 'pass' else ''}")
        else:
            verdict = await _judge(
                judge_llm, _REFUSAL_SYSTEM,
                f"QUESTION:\n{case['question']}\n\n"
                f"WHY IT IS UNANSWERABLE:\n{case.get('note', '')}\n\n"
                f"CONTEXT CHUNKS:\n{context}\n\n"
                f"GENERATED ANSWER:\n{answer or '(generation skipped)'}",
            )
            ok = verdict.get("correct_refusal")
            diag = "pass" if ok else "FABRICATED"
            print(f"{case['id']:>3} {case['name'][:34]:<34} "
                  f"{'-':>5} {'-':>5} {'-':>5}  {diag}  {verdict.get('reason','')[:44]}")

        records.append({
            "case": case, "verdict": verdict, "diagnosis": diag,
            "answer": answer, "chunks": response.chunks, "images": response.images,
        })
        if args.delay:
            await asyncio.sleep(args.delay)

    # ── Summary ───────────────────────────────────────────────────────
    ans = [r for r in records if r["case"].get("answerable", True)]
    una = [r for r in records if not r["case"].get("answerable", True)]
    counts: dict[str, int] = {}
    for r in records:
        counts[r["diagnosis"]] = counts.get(r["diagnosis"], 0) + 1

    print("-" * 92)
    print("\nDIAGNOSIS BREAKDOWN")
    for k in ("pass", "PARTIAL", "RETRIEVAL", "GENERATION", "HALLUCINATION",
              "FABRICATED", "JUDGE ERROR"):
        if counts.get(k):
            print(f"  {k:<14} {counts[k]}")

    if ans:
        suff = sum(1 for r in ans if r["verdict"].get("context_sufficient"))
        correct = sum(1 for r in ans if r["verdict"].get("answer_correct") == 2)
        grounded = sum(1 for r in ans if r["verdict"].get("grounded"))
        print(f"\n  context sufficiency : {suff}/{len(ans)} ({suff/len(ans):.0%})")
        print(f"  answer correctness  : {correct}/{len(ans)} ({correct/len(ans):.0%})")
        print(f"  groundedness        : {grounded}/{len(ans)} ({grounded/len(ans):.0%})")
    if una:
        ok = sum(1 for r in una if r["verdict"].get("correct_refusal"))
        print(f"  correct refusals    : {ok}/{len(una)}")

    print("\n  Read it this way: low SUFFICIENCY means fix retrieval (chunking,")
    print("  table handling, image path). High sufficiency with low CORRECTNESS")
    print("  means fix generation (prompt, model, context budget).")

    if args.report:
        report_path = Path(args.report)
        content = _markdown(records, settings, judge_model, counts)
        # append with separator so successive runs accumulate in one file
        if report_path.exists():
            with report_path.open("a", encoding="utf-8") as fh:
                fh.write("\n\n---\n\n" + content)
        else:
            report_path.write_text(content, encoding="utf-8")
        print(f"\nreport written to {args.report}")


def _markdown(records, settings, judge_model, counts) -> str:
    ans = [r for r in records if r["case"].get("answerable", True)]
    una = [r for r in records if not r["case"].get("answerable", True)]
    out = [
        "# LLM-as-judge evaluation", "",
        f"_{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_", "",
        "| | |", "|---|---|",
        f"| generator (text) | `{settings.llm.model_name}` |",
        f"| generator (vision) | `{settings.llm.vision_model_name if settings.llm.vision_enabled else 'off'}` |",
        f"| **judge** | `{judge_model}` |",
        f"| multimodal | `{settings.multimodal.enabled}` / mode `{settings.multimodal.retrieval.mode.value}` |",
        "", "## Diagnosis", "", "| Outcome | Count | Means |", "|---|---|---|",
    ]
    means = {
        "pass": "retrieved the right context AND answered correctly",
        "PARTIAL": "answer missing material facts",
        "RETRIEVAL": "**chunks did not contain the answer** — fix chunking/indexing",
        "GENERATION": "context was sufficient but the answer was wrong — fix the prompt/model",
        "HALLUCINATION": "**asserted facts absent from the context**",
        "FABRICATED": "**answered an unanswerable question**",
        "JUDGE ERROR": "judge did not return valid JSON",
    }
    for k, v in counts.items():
        out.append(f"| {k} | {v} | {means.get(k, '')} |")

    if ans:
        suff = sum(1 for r in ans if r["verdict"].get("context_sufficient"))
        corr = sum(1 for r in ans if r["verdict"].get("answer_correct") == 2)
        grnd = sum(1 for r in ans if r["verdict"].get("grounded"))
        out += ["", "## Metrics", "", "| Metric | Score |", "|---|---|",
                f"| Context sufficiency (retrieval) | {suff}/{len(ans)} ({suff/len(ans):.0%}) |",
                f"| Answer correctness (end-to-end) | {corr}/{len(ans)} ({corr/len(ans):.0%}) |",
                f"| Groundedness (no hallucination) | {grnd}/{len(ans)} ({grnd/len(ans):.0%}) |"]
    if una:
        ok = sum(1 for r in una if r["verdict"].get("correct_refusal"))
        out.append(f"| Correct refusals | {ok}/{len(una)} |")

    out += ["", "## Cases", "",
            "| id | name | sufficient | correct | grounded | diagnosis |",
            "|---|---|---|---|---|---|"]
    for r in records:
        v = r["verdict"]
        out.append(
            f"| `{r['case']['id']}` | {r['case']['name']} | "
            f"{v.get('context_sufficient', '—')} | {v.get('answer_correct', '—')} | "
            f"{v.get('grounded', '—')} | "
            f"{'**' + r['diagnosis'] + '**' if r['diagnosis'] != 'pass' else 'pass'} |"
        )

    bad = [r for r in records if r["diagnosis"] not in ("pass",)]
    if bad:
        out += ["", "## Failures in detail", ""]
        for r in bad:
            c, v = r["case"], r["verdict"]
            out += [
                f"### `{c['id']}` {c['name']} — {r['diagnosis']}", "",
                f"**Question:** {c['question']}", "",
                f"**Gold answer:** {c.get('expected_answer') or '_(unanswerable)_'}", "",
                f"**Generated:** {r['answer'][:800] or '_(skipped)_'}", "",
                f"**Judge:** {v.get('reason', v.get('error', ''))}", "",
            ]
            if v.get("missing_facts"):
                out += ["**Missing facts:** " + ", ".join(map(str, v["missing_facts"])), ""]
            out += ["**Retrieved:**", "", "| # | type | source | text |", "|---|---|---|---|"]
            for i, ch in enumerate(r["chunks"][:5], 1):
                out.append(f"| {i} | `{ch.content_type}` | {ch.source[:30]} | "
                           f"{ch.text[:120].replace(chr(10),' ').replace('|','\\|')} |")
            if r["images"]:
                out += ["", "Images: " + ", ".join(
                    f"`{im.role}` p{im.page_number}" for im in r["images"])]
            out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    asyncio.run(main())
