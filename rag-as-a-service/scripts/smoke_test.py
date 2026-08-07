"""End-to-end smoke test against the live index.

Exercises every retrieval path the design defines, and asserts the behaviour
that matters rather than just printing results:

  1. index state          — both collections populated, dual representation intact
  2. text-only query      — no images returned (intent router gates correctly)
  3. table query          — a content_type=table chunk ranks in the top results
  4. figure query         — images[] populated, gate not letting everything through
  5. table crop promotion — a retrieved table chunk pulls its own crop
  6. generation           — text path, and vision path with real pixels (--generate)

Runs directly against the pipelines, so it does NOT need the API server — which
matters because embedded Qdrant will not allow both at once.

Usage:
    python scripts/smoke_test.py
    python scripts/smoke_test.py --generate        # also calls Groq
    python scripts/smoke_test.py --query "your own question"
"""

import argparse
import asyncio
import sys
import time

sys.path.insert(0, "src")

from gernas_rag.config.settings import get_settings  # noqa: E402
from gernas_rag.embeddings.factory import get_embedder  # noqa: E402
from gernas_rag.models.retrieval import RetrieveRequest  # noqa: E402
from gernas_rag.retrieval.multimodal_pipeline import (  # noqa: E402
    MultimodalRetrievalPipeline,
)
from gernas_rag.retrieval.pipeline import RetrievalPipeline  # noqa: E402
from gernas_rag.vectordb.factory import get_vectordb  # noqa: E402

_PASS, _FAIL = "PASS", "FAIL"
_results: list[tuple[str, str, str]] = []


def _check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, _PASS if ok else _FAIL, detail))
    print(f"  [{_PASS if ok else _FAIL}] {name}" + (f" — {detail}" if detail else ""))


def _show(response, limit: int = 4) -> None:
    for c in response.chunks[:limit]:
        kind = "TABLE" if c.content_type == "table" else (
            "STUB " if c.content_type == "image_stub" else "text "
        )
        print(f"      [{kind}] {c.score:.3f} {c.source[:28]:<28} "
              f"{c.text[:70].replace(chr(10), ' ')}")
    for im in response.images:
        tag = "promoted" if im.promoted_from_text else f"rank {im.rank}"
        print(f"      [IMG  ] {im.score:.3f} {im.role:<12} p{im.page_number} "
              f"({tag}) {im.caption[:44]}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generate", action="store_true", help="call Groq (costs quota)")
    parser.add_argument("--query", help="run a single ad-hoc query and exit")
    args = parser.parse_args()

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

    text_pipeline = RetrievalPipeline(settings, embedder, vectordb)
    pipeline = MultimodalRetrievalPipeline(
        settings, text_pipeline, mm_embedder, image_store, score_floor
    )

    # ── 1. Index state ────────────────────────────────────────────────
    print("\n=== 1. INDEX STATE ===")
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    async def count(content_type: str | None = None) -> int:
        flt = (
            Filter(must=[FieldCondition(key="content_type",
                                        match=MatchValue(value=content_type))])
            if content_type
            else None
        )
        result = await vectordb._client.count(  # noqa: SLF001
            collection_name=settings.vectordb.collection_name,
            count_filter=flt,
            exact=True,
        )
        return int(result.count)

    total, tables, stubs = await count(), await count("table"), await count("image_stub")
    images = await image_store.count() if image_store else 0
    print(f"  text chunks   : {total}  (tables={tables}, image_stubs={stubs})")
    print(f"  image vectors : {images}")
    _check("text collection populated", total > 0, f"{total} chunks")
    _check("tables indexed", tables > 0, f"{tables} table chunks")
    if settings.multimodal.enabled:
        _check("image collection populated", images > 0, f"{images} vectors")
        _check(
            "dual representation (every table has a crop)",
            images >= tables,
            f"{images} image vectors vs {tables} tables",
        )

    if args.query:
        print(f"\n=== AD-HOC: {args.query} ===")
        r = await pipeline.retrieve(
            RetrieveRequest(query=args.query, top_k=5, include_images=True)
        )
        _show(r, limit=5)
        return

    # ── 2. Text-only query ────────────────────────────────────────────
    print("\n=== 2. TEXT-ONLY QUERY (images must be gated OUT) ===")
    q = "what is the minimum pricing floor for a BB-rated corporate term loan"
    print(f"  Q: {q}")
    t0 = time.perf_counter()
    r = await pipeline.retrieve(RetrieveRequest(query=q, top_k=5))
    print(f"  ({time.perf_counter() - t0:.1f}s — first query includes model warmup)")
    _show(r)
    _check("returns chunks", bool(r.chunks), f"{len(r.chunks)}")
    _check("no images on a pure-text question", not r.images,
           f"{len(r.images)} images returned")

    # ── 3. Table query ────────────────────────────────────────────────
    print("\n=== 3. TABLE QUERY (a table chunk must surface) ===")
    q = "maximum exposure limit by counterparty classification as percent of Tier 1 capital"
    print(f"  Q: {q}")
    r = await pipeline.retrieve(RetrieveRequest(query=q, top_k=5))
    _show(r)
    ranks = [i for i, c in enumerate(r.chunks) if c.content_type == "table"]
    _check("a table chunk is retrieved", bool(ranks), f"at rank(s) {ranks}")
    _check("a table chunk is in the top 3", any(i < 3 for i in ranks))

    # ── 4. Figure query ───────────────────────────────────────────────
    print("\n=== 4. FIGURE QUERY (images must be returned) ===")
    q = "show me the credit approval authority matrix diagram"
    print(f"  Q: {q}")
    r = await pipeline.retrieve(RetrieveRequest(query=q, top_k=5))
    _show(r)
    if settings.multimodal.enabled:
        _check("image search ran", r.image_search_performed)
        _check("images returned", bool(r.images), f"{len(r.images)}")
        _check("gate did not pass everything",
               len(r.images) <= settings.multimodal.retrieval.image_final_k,
               f"{len(r.images)} <= {settings.multimodal.retrieval.image_final_k}")
        _check("space id reported", bool(r.multimodal_space_id),
               r.multimodal_space_id or "")

    # ── 5. Table crop promotion ───────────────────────────────────────
    print("\n=== 5. TABLE CROP PROMOTION (text hit pulls its own crop) ===")
    if settings.llm.vision_enabled and settings.multimodal.enabled:
        q = "sector concentration soft limit and hard limit for real estate"
        print(f"  Q: {q}")
        r = await pipeline.retrieve(
            RetrieveRequest(query=q, top_k=5, include_images=True)
        )
        _show(r)
        promoted = [im for im in r.images if im.promoted_from_text]
        _check("a table crop was promoted from its text chunk", bool(promoted),
               f"{len(promoted)} promoted")
    else:
        print("  skipped (needs llm.vision_enabled + multimodal.enabled)")

    # ── 6. Generation ─────────────────────────────────────────────────
    print("\n=== 6. GENERATION ===")
    if not args.generate:
        print("  skipped — pass --generate to call Groq")
    else:
        from gernas_rag.generation.generator import ResponseGenerator
        from gernas_rag.llm.factory import get_llm

        payload_builder = None
        if settings.llm.vision_enabled:
            from gernas_rag.generation.image_payload import ImagePayloadBuilder
            from gernas_rag.images.store import get_asset_store

            payload_builder = ImagePayloadBuilder(
                get_asset_store(settings.multimodal.storage), settings.llm
            )
        generator = ResponseGenerator(settings, get_llm(settings.llm), payload_builder)

        q = "what is the minimum pricing floor for a BB-rated corporate term loan"
        r = await pipeline.retrieve(RetrieveRequest(query=q, top_k=5))
        print(f"\n  [TEXT PATH] Q: {q}")
        answer = await generator.generate(q, r.chunks, r.images)
        print(f"  A: {answer[:600]}")
        _check("text generation returned an answer", len(answer) > 40)

        q = "show me the credit approval authority matrix and describe what it shows"
        r = await pipeline.retrieve(
            RetrieveRequest(query=q, top_k=5, include_images=True)
        )
        print(f"\n  [VISION PATH] Q: {q}  ({len(r.images)} images sent)")
        answer = await generator.generate(q, r.chunks, r.images)
        print(f"  A: {answer[:800]}")
        _check("vision generation returned an answer", len(answer) > 40)
        _check("answer cites a figure", "[I1]" in answer or "[I" in answer,
               "no [IN] citation" if "[I" not in answer else "")

    # ── Summary ───────────────────────────────────────────────────────
    failed = [r for r in _results if r[1] == _FAIL]
    print(f"\n{'=' * 60}")
    print(f"{len(_results) - len(failed)}/{len(_results)} checks passed")
    for name, _, detail in failed:
        print(f"  FAIL: {name} {detail}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
