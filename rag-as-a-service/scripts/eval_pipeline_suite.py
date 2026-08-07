"""Run tests/fixtures/pipeline_suite.yaml against the live index.

Deterministic and LLM-free. Every case declares what a correct retrieval looks
like for THIS pipeline, and the runner asserts it — so a failure names the
broken path rather than handing you an aggregate score.

Usage:
    python scripts/eval_pipeline_suite.py
    python scripts/eval_pipeline_suite.py --verbose
    python scripts/eval_pipeline_suite.py --only B01,B02,P01
    python scripts/eval_pipeline_suite.py --covers table     # substring of `covers`
"""

import argparse
import asyncio
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, "src")

from gernas_rag.config.settings import get_settings  # noqa: E402
from gernas_rag.embeddings.factory import get_embedder  # noqa: E402
from gernas_rag.models.retrieval import DocumentFilter, RetrieveRequest  # noqa: E402
from gernas_rag.retrieval.multimodal_pipeline import (  # noqa: E402
    MultimodalRetrievalPipeline,
)
from gernas_rag.retrieval.pipeline import RetrievalPipeline  # noqa: E402
from gernas_rag.vectordb.factory import get_vectordb  # noqa: E402

SUITE = Path("tests/fixtures/pipeline_suite.yaml")


def norm(s: str) -> str:
    return re.sub(r"[\s,]+", "", s.lower())


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument("--covers", help="filter by substring of the `covers` field")
    parser.add_argument(
        "--report",
        nargs="?",
        const="eval_report.md",
        help="write a markdown report (default: eval_report.md)",
    )
    args = parser.parse_args()

    suite = yaml.safe_load(SUITE.read_text(encoding="utf-8"))
    cases = suite["cases"]
    if args.only:
        wanted = {s.strip() for s in args.only.split(",")}
        cases = [c for c in cases if c["id"] in wanted]
    if args.covers:
        cases = [c for c in cases if args.covers.lower() in c.get("covers", "").lower()]

    settings = get_settings()
    embedder = get_embedder(settings.embedding)
    try:
        vectordb = get_vectordb(settings.vectordb)
    except RuntimeError as exc:
        print(f"\n{exc}\n")
        sys.exit(2)

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
        settings,
        RetrievalPipeline(settings, embedder, vectordb),
        mm_embedder,
        image_store,
        floor,
    )

    print(f"multimodal={settings.multimodal.enabled} "
          f"mode={settings.multimodal.retrieval.mode.value} "
          f"vision={settings.llm.vision_enabled} "
          f"protect_tables={settings.chunking.protect_tables}\n")
    print(f"{'id':<5} {'covers':<52} {'rank':>4} {'imgs':>5}  result")
    print("-" * 108)

    passed = failed = reported = 0
    problems: list[str] = []
    records: list[dict] = []  # one per case, for the markdown report

    for case in cases:
        request = RetrieveRequest(
            query=case["query"],
            top_k=case.get("top_k", 5),
            include_parent=False,
            include_images=case.get("include_images"),
            filters=DocumentFilter(**case["filters"]) if case.get("filters") else DocumentFilter(),
        )
        try:
            response = await pipeline.retrieve(request)
        except Exception as exc:  # noqa: BLE001
            print(f"{case['id']:<5} {case.get('covers','')[:52]:<52} {'-':>4} {'-':>5}  ERROR {exc}")
            failed += 1
            problems.append(f"{case['id']}: raised {type(exc).__name__}: {exc}")
            records.append({
                "id": case["id"], "covers": case.get("covers", ""),
                "query": case["query"], "status": "ERROR",
                "rank": None, "images": 0, "top": 0.0,
                "errs": [f"{type(exc).__name__}: {exc}"],
                "chunks": [], "imgs": [],
            })
            continue

        haystack = " ".join(c.text for c in response.chunks)
        types = {c.content_type for c in response.chunks}
        docs = {c.source for c in response.chunks}

        rank = None
        if case.get("expect_doc"):
            for i, c in enumerate(response.chunks):
                if case["expect_doc"] in c.source:
                    rank = i + 1
                    break

        errs: list[str] = []
        if case.get("expect_doc") and rank is None:
            errs.append(f"doc miss (got {sorted(d[:22] for d in docs)})")
        if case.get("max_rank") and rank and rank > case["max_rank"]:
            errs.append(f"rank {rank} > {case['max_rank']}")
        if case.get("expect_content_type") and case["expect_content_type"] not in types:
            errs.append(f"no {case['expect_content_type']} chunk (got {sorted(types)})")
        for token in case.get("must_contain", []):
            if norm(token) not in norm(haystack):
                errs.append(f"missing {token!r}")
        if case.get("expect_images") is True and not response.images:
            errs.append("no images returned")
        if case.get("expect_images") is False and response.images:
            errs.append(f"{len(response.images)} unexpected images")
        if case.get("expect_image_role"):
            roles = {im.role for im in response.images}
            if case["expect_image_role"] not in roles:
                errs.append(f"no {case['expect_image_role']} (got {sorted(roles)})")
        if case.get("expect_promoted") and not any(
            im.promoted_from_text for im in response.images
        ):
            errs.append("no promoted crop")
        if case.get("min_distinct_docs") and len(docs) < case["min_distinct_docs"]:
            errs.append(f"{len(docs)} distinct docs < {case['min_distinct_docs']}")

        top = response.chunks[0].score if response.chunks else 0.0
        if case.get("report_only"):
            reported += 1
            verdict = f"REPORT top_score={top:.3f} imgs={len(response.images)}"
            if case.get("expect_images") is False and response.images:
                verdict += "  <- images leaked on an out-of-corpus query"
        elif errs:
            failed += 1
            verdict = "FAIL: " + "; ".join(errs)
            problems.append(f"{case['id']} ({case.get('covers','')}): {'; '.join(errs)}")
        else:
            passed += 1
            verdict = "pass"

        print(f"{case['id']:<5} {case.get('covers','')[:52]:<52} "
              f"{(rank if rank else '-'):>4} {len(response.images):>5}  {verdict}")

        records.append({
            "id": case["id"],
            "covers": case.get("covers", ""),
            "query": case["query"],
            "status": "REPORT" if case.get("report_only") else ("FAIL" if errs else "pass"),
            "rank": rank,
            "images": len(response.images),
            "top": top,
            "errs": errs,
            "chunks": [
                {"type": c.content_type, "score": c.score, "source": c.source,
                 "text": c.text[:160].replace("\n", " ")}
                for c in response.chunks[:5]
            ],
            "imgs": [
                {"role": im.role, "score": im.score, "source": im.source,
                 "page": im.page_number, "promoted": im.promoted_from_text,
                 "caption": (im.caption or "")[:80]}
                for im in response.images
            ],
        })

        if args.verbose:
            for c in response.chunks[:3]:
                print(f"        [{c.content_type[:10]:<10}] {c.score:.3f} "
                      f"{c.source[:32]:<32} {c.text[:56].replace(chr(10),' ')}")
            for im in response.images:
                tag = "promoted" if im.promoted_from_text else "ann"
                print(f"        [IMG {im.role[:11]:<11}] {im.score:.3f} "
                      f"{im.source[:32]:<32} ({tag}) {im.caption[:40]}")

    print("-" * 108)
    print(f"\n{passed} passed, {failed} failed, {reported} report-only "
          f"(of {len(cases)} cases)")
    if problems:
        print("\nFAILURES")
        for p in problems:
            print(f"  {p}")

    if args.report:
        path = Path(args.report)
        path.write_text(
            _markdown(records, settings, passed, failed, reported), encoding="utf-8"
        )
        print(f"\nreport written to {path}")

    if problems:
        sys.exit(1)


def _markdown(records, settings, passed, failed, reported) -> str:
    """Render the run as a reviewable markdown report."""
    total = passed + failed
    rate = f"{passed / total:.0%}" if total else "n/a"
    icon = {"pass": "PASS", "FAIL": "**FAIL**", "REPORT": "info", "ERROR": "**ERROR**"}

    out: list[str] = [
        "# Retrieval evaluation report",
        "",
        f"_{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
        "Deterministic, LLM-free. Measures **retrieval**, not answer quality —",
        "a failure here means the generator never had a chance. Answer quality is",
        "RAGAS's job (`/api/v1/evaluate`).",
        "",
        "## Configuration",
        "",
        "| Setting | Value |",
        "|---|---|",
        f"| text embedder | `{settings.embedding.model_name}` |",
        f"| multimodal enabled | `{settings.multimodal.enabled}` |",
        f"| multimodal model | `{settings.multimodal.embedding.model_name}` |",
        f"| retrieval mode | `{settings.multimodal.retrieval.mode.value}` |",
        f"| image intent | `{settings.multimodal.retrieval.image_intent.value}` |",
        f"| image score floor | `{settings.multimodal.retrieval.image_score_floor or 'registry default'}` |",
        f"| vision generation | `{settings.llm.vision_enabled}` |",
        f"| protect_tables (D8) | `{settings.chunking.protect_tables}` |",
        f"| extraction strategy | `{settings.chunking.extraction_strategy.value}` |",
        "",
        "## Summary",
        "",
        f"**{passed} passed · {failed} failed · {reported} report-only** "
        f"— pass rate {rate}",
        "",
    ]

    # Coverage by path group.
    groups = {
        "T": "text -> text", "B": "text -> table (D8)", "I": "text -> image",
        "S": "image_stub", "P": "table crop promotion", "R": "intent routing",
        "F": "metadata filters", "X": "cross-document", "O": "OCR / scanned",
        "N": "out-of-corpus (report-only)",
    }
    out += ["## Coverage by path", "", "| Path | Cases | Passed | Failed |", "|---|---|---|---|"]
    for prefix, label in groups.items():
        sel = [r for r in records if r["id"].startswith(prefix)]
        if not sel:
            continue
        p = sum(1 for r in sel if r["status"] == "pass")
        f = sum(1 for r in sel if r["status"] in ("FAIL", "ERROR"))
        out.append(f"| {label} | {len(sel)} | {p} | {f} |")
    out.append("")

    # Per-case results.
    out += [
        "## Results", "",
        "| id | covers | rank | imgs | result |", "|---|---|---|---|---|",
    ]
    for r in records:
        detail = "; ".join(r["errs"]) if r["errs"] else (
            f"top_score={r['top']:.3f}" if r["status"] == "REPORT" else ""
        )
        out.append(
            f"| `{r['id']}` | {r['covers']} | {r['rank'] or '—'} | {r['images']} | "
            f"{icon.get(r['status'], r['status'])} {detail} |"
        )
    out.append("")

    # Failures in full.
    fails = [r for r in records if r["status"] in ("FAIL", "ERROR")]
    if fails:
        out += ["## Failures", ""]
        for r in fails:
            out += [
                f"### `{r['id']}` — {r['covers']}", "",
                f"**Query:** {r['query']}", "",
                "**Why it failed:**", "",
            ]
            out += [f"- {e}" for e in r["errs"]]
            out += ["", "**What came back:**", "",
                    "| # | type | score | source | text |", "|---|---|---|---|---|"]
            for i, c in enumerate(r["chunks"], 1):
                text = c["text"].replace("|", "\\|")
                out.append(f"| {i} | `{c['type']}` | {c['score']:.3f} | "
                           f"{c['source'][:34]} | {text} |")
            if r["imgs"]:
                out += ["", "| image role | score | source | page | promoted |",
                        "|---|---|---|---|---|"]
                for im in r["imgs"]:
                    out.append(f"| `{im['role']}` | {im['score']:.3f} | "
                               f"{im['source'][:30]} | {im['page']} | {im['promoted']} |")
            out.append("")

    # Report-only cases: the threshold calibration data.
    reports = [r for r in records if r["status"] == "REPORT"]
    if reports:
        out += [
            "## Out-of-corpus scores", "",
            "Dense ANN always returns something — there is no 'no answer' in a",
            "vector search. Declining is the **generator's** job. Compare these",
            "top scores against the answerable cases to pick a refusal threshold.",
            "",
            "| id | query | top score | images |", "|---|---|---|---|",
        ]
        for r in reports:
            out.append(f"| `{r['id']}` | {r['query'][:60]} | {r['top']:.3f} | {r['images']} |")
        answerable_tops = [r["top"] for r in records if r["status"] == "pass" and r["top"]]
        if answerable_tops:
            lo = min(answerable_tops)
            out += ["", f"Lowest top-score among **passing** cases: `{lo:.3f}`. "
                        "A refusal threshold below that risks declining valid questions.", ""]

    out += [
        "## How this evaluates", "",
        "| Assertion | Mechanic |",
        "|---|---|",
        "| `expect_doc` | substring match on `chunk.source`; records 1-based rank |",
        "| `max_rank` | that rank must be <= N |",
        "| `expect_content_type` | value must appear in the returned chunks' content types |",
        "| `must_contain` | normalised (lowercase, whitespace/comma-stripped) substring over ALL returned chunk text |",
        "| `expect_images` | `len(response.images)` is non-zero / zero |",
        "| `expect_promoted` | any returned image has `promoted_from_text=true` |",
        "| `min_distinct_docs` | count of distinct `chunk.source` values |",
        "",
        "**Known limits.** `must_contain` searches the concatenation of all",
        "returned chunks, so a fact found at rank 5 still counts — ranking is",
        "measured separately by `max_rank`. Matching is literal, not semantic:",
        "`60 bps` will not match `60 basis points`.",
        "",
    ]
    return "\n".join(out)


if __name__ == "__main__":
    asyncio.run(main())
