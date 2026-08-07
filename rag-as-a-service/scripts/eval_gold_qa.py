"""Retrieval evaluation against tests/fixtures/gold_qa.json.

Deterministic and LLM-free — answers the question "did the RIGHT chunk come
back?", which RAGAS cannot tell you cheaply or per-query.

Two jobs:

  VALIDATE  every expected_source document must exist in the index. A gold set
            that references documents you never ingested produces meaningless
            scores, so this runs first and reports separately.

  MEASURE   per case: was the expected document retrieved, at what rank, and did
            any retrieved chunk actually contain the key facts from
            expected_answer? Reports Recall@1/3/5, MRR and fact coverage.

Modality note: the gold set marks a source 'figure' when the answer lives in a
visual element. This pipeline may legitimately serve that as a TABLE TEXT chunk
(D8 dual representation) rather than an image — which is a better outcome, since
exact values become lexically searchable. Modality is therefore REPORTED, not
scored as a failure.

Usage:
    python scripts/eval_gold_qa.py
    python scripts/eval_gold_qa.py --top-k 10 --verbose
    python scripts/eval_gold_qa.py --only 9,10,31       # specific case ids
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, "src")

from gernas_rag.config.settings import get_settings  # noqa: E402
from gernas_rag.embeddings.factory import get_embedder  # noqa: E402
from gernas_rag.models.retrieval import RetrieveRequest  # noqa: E402
from gernas_rag.retrieval.multimodal_pipeline import (  # noqa: E402
    MultimodalRetrievalPipeline,
)
from gernas_rag.retrieval.pipeline import RetrievalPipeline  # noqa: E402
from gernas_rag.vectordb.factory import get_vectordb  # noqa: E402

GOLD = Path("tests/fixtures/gold_qa.json")

# Facts worth checking for: money amounts, percentages, bps, dates, article refs.
_FACT_RE = re.compile(
    r"\b(?:AED\s?[\d.,]+\s?(?:billion|bn|million|m)?"
    r"|\d+(?:\.\d+)?%"
    r"|\d+\s?bps"
    r"|\d{1,2}\s+\w+\s+\d{4}"
    r"|\d{2}-\w{3}-\d{4}"
    r"|Article\s+\d+(?:\.\d+)*"
    r"|Tier\s+\d)\b",
    re.IGNORECASE,
)


def key_facts(expected_answer: str, limit: int = 6) -> list[str]:
    """Extract checkable atoms from the gold answer."""
    seen, out = set(), []
    for m in _FACT_RE.findall(expected_answer):
        norm = m.strip().lower()
        if norm not in seen:
            seen.add(norm)
            out.append(m.strip())
        if len(out) >= limit:
            break
    return out


def fact_hit(fact: str, haystack: str) -> bool:
    """Loose containment: normalise whitespace and currency spacing."""
    f = re.sub(r"\s+", "", fact.lower()).replace(",", "")
    h = re.sub(r"\s+", "", haystack.lower()).replace(",", "")
    return f in h


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--only", help="comma-separated case ids")
    args = parser.parse_args()

    if not GOLD.exists():
        print(f"Gold set not found at {GOLD}")
        sys.exit(1)
    cases = json.loads(GOLD.read_text(encoding="utf-8"))
    if args.only:
        wanted = {s.strip() for s in args.only.split(",")}
        cases = [c for c in cases if c["id"] in wanted]

    settings = get_settings()
    embedder = get_embedder(settings.embedding)
    try:
        vectordb = get_vectordb(settings.vectordb)
    except RuntimeError as exc:
        print(f"\n{exc}\n")
        sys.exit(2)

    mm_embedder = image_store = None
    score_floor = 0.10
    if settings.multimodal.enabled:
        from gernas_rag.embeddings.multimodal.factory import (
            get_multimodal_embedder,
            registry_score_floor,
        )
        from gernas_rag.vectordb.image_factory import get_image_store

        mm_embedder = get_multimodal_embedder(settings.multimodal.embedding)
        score_floor = registry_score_floor(settings.multimodal.embedding)
        space = mm_embedder.space
        collection = settings.multimodal.image_collection_name or space.collection_name(
            settings.multimodal.image_collection_base
        )
        image_store = get_image_store(settings.vectordb, collection, vectordb)

    pipeline = MultimodalRetrievalPipeline(
        settings,
        RetrievalPipeline(settings, embedder, vectordb),
        mm_embedder,
        image_store,
        score_floor,
    )

    # ── VALIDATE: do the referenced documents exist in the index? ─────
    print("=== GOLD SET VALIDATION ===")
    indexed_docs: set[str] = set()
    offset = None
    while True:
        page, offset = await vectordb._client.scroll(  # noqa: SLF001
            collection_name=settings.vectordb.collection_name,
            limit=512,
            offset=offset,
            with_payload=True,
        )
        for r in page:
            name = (r.payload or {}).get("document_name")
            if name:
                indexed_docs.add(name)
        if offset is None:
            break

    referenced = {
        s["document"]
        for c in cases
        for s in c.get("expected_sources", [])
        if s.get("document")
    }
    missing = sorted(referenced - indexed_docs)
    print(f"  documents in index      : {len(indexed_docs)}")
    print(f"  documents referenced    : {len(referenced)}")
    if missing:
        print("  NOT IN INDEX (scores for these cases are meaningless):")
        for m in missing:
            print(f"    - {m}")
    else:
        print("  all referenced documents are indexed")

    answerable = [c for c in cases if c.get("answerable", True)]
    unanswerable = [c for c in cases if not c.get("answerable", True)]
    print(f"  cases: {len(answerable)} answerable, {len(unanswerable)} unanswerable\n")

    # ── MEASURE ───────────────────────────────────────────────────────
    print("=== RETRIEVAL ===")
    print(f"{'id':>3} {'name':<38} {'doc@':>5} {'facts':>7} {'imgs':>5}  verdict")
    print("-" * 96)

    hits = {1: 0, 3: 0, 5: 0}
    reciprocal = 0.0
    fact_num = fact_den = 0
    failures: list[str] = []

    for case in answerable:
        expected_docs = {s["document"] for s in case.get("expected_sources", [])}
        wants_figure = any(
            s.get("modality") == "figure" for s in case.get("expected_sources", [])
        )
        response = await pipeline.retrieve(
            RetrieveRequest(query=case["question"], top_k=args.top_k, include_parent=False)
        )

        # Rank of the first chunk from an expected document.
        rank = None
        for i, chunk in enumerate(response.chunks):
            if any(d in chunk.source for d in expected_docs):
                rank = i + 1
                break
        if rank:
            reciprocal += 1 / rank
            for k in hits:
                if rank <= k:
                    hits[k] += 1

        # Did the retrieved text actually carry the answer's key facts?
        haystack = " ".join(c.text for c in response.chunks)
        haystack += " " + " ".join(
            f"{im.caption} {im.nearest_heading}" for im in response.images
        )
        facts = key_facts(case.get("expected_answer", ""))
        found = [f for f in facts if fact_hit(f, haystack)]
        fact_num += len(found)
        fact_den += len(facts)

        types = {c.content_type for c in response.chunks}
        verdict = []
        if rank is None:
            verdict.append("DOC MISSED")
            failures.append(
                f"[{case['id']}] {case['name']}: expected {sorted(expected_docs)}, "
                f"got {sorted({c.source for c in response.chunks})}"
            )
        elif rank > 3:
            verdict.append(f"weak (rank {rank})")
        if facts and not found:
            verdict.append("NO FACTS")
        if wants_figure and "table" in types:
            verdict.append("served as table")
        elif wants_figure and response.images:
            verdict.append("served as image")
        elif wants_figure:
            verdict.append("figure expected, text only")

        print(
            f"{case['id']:>3} {case['name'][:38]:<38} "
            f"{(rank if rank else '-'):>5} "
            f"{f'{len(found)}/{len(facts)}':>7} "
            f"{len(response.images):>5}  {', '.join(verdict) if verdict else 'OK'}"
        )
        if args.verbose:
            for c in response.chunks[:3]:
                print(f"        [{c.content_type[:5]:<5}] {c.score:.3f} "
                      f"{c.source[:34]:<34} {c.text[:60].replace(chr(10), ' ')}")

    n = max(1, len(answerable))
    print("-" * 96)
    print(f"\nRecall@1 {hits[1] / n:.0%}   Recall@3 {hits[3] / n:.0%}   "
          f"Recall@5 {hits[5] / n:.0%}   MRR {reciprocal / n:.3f}")
    print(f"Fact coverage in retrieved text: {fact_num}/{fact_den} "
          f"({fact_num / max(1, fact_den):.0%})")

    # ── Unanswerable controls ─────────────────────────────────────────
    if unanswerable:
        print("\n=== UNANSWERABLE CONTROLS (retrieval should be weak) ===")
        for case in unanswerable:
            response = await pipeline.retrieve(
                RetrieveRequest(query=case["question"], top_k=args.top_k)
            )
            top = response.chunks[0].score if response.chunks else 0.0
            print(f"{case['id']:>3} {case['name'][:44]:<44} "
                  f"top_score={top:.3f} images={len(response.images)}")
        print("\n  Retrieval always returns SOMETHING (dense ANN has no notion of")
        print("  'no answer'). Declining is the GENERATOR's job — compare these")
        print("  scores with the answerable cases above to pick a cut-off.")

    if failures:
        print(f"\n{len(failures)} case(s) missed the expected document:")
        for f in failures:
            print(f"  {f}")


if __name__ == "__main__":
    asyncio.run(main())
