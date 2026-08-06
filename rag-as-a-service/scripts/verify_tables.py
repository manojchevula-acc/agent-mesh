"""Round-trip table verification: SOURCE -> CHUNKED -> INDEXED.

Answers three questions with evidence rather than inspection:

  1. Are all tables stored?     source tables vs indexed table chunks, per document
  2. Text or image vectors?     reports both collections for every table
  3. Cut midway?                every part must carry a header + delimiter row,
                                and row counts must reconcile with the source

Unlike audit_tables.py (which only inspects what is already in the index), this
re-extracts each document so it can compare against ground truth.

Usage:
    python scripts/verify_tables.py --path ./docs
    python scripts/verify_tables.py --path ./docs --show-tables
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, "src")

from gernas_rag.chunking.base import _TABLE_BLOCK, BaseChunker  # noqa: E402
from gernas_rag.chunking.factory import get_chunker  # noqa: E402
from gernas_rag.config.settings import get_settings  # noqa: E402
from gernas_rag.extraction.factory import get_extractor  # noqa: E402
from gernas_rag.ingestion.metadata import MetadataExtractor  # noqa: E402
from gernas_rag.vectordb.factory import get_vectordb  # noqa: E402

_DELIM = re.compile(r"^\s*\|[\s:\-|]+\|\s*$", re.MULTILINE)


def _body_rows(table_md: str) -> int:
    lines = [ln for ln in table_md.strip().split("\n") if ln.strip().startswith("|")]
    return max(0, len(lines) - 2)  # minus header + delimiter


async def _indexed_tables(settings) -> dict[str, list[dict]]:
    """content_type=table payloads from the text collection, keyed by document."""
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    vectordb = get_vectordb(settings.vectordb)
    by_doc: dict[str, list[dict]] = {}
    offset = None
    try:
        while True:
            page, offset = await vectordb._client.scroll(  # noqa: SLF001
                collection_name=settings.vectordb.collection_name,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(key="content_type", match=MatchValue(value="table"))
                    ]
                ),
                limit=256,
                offset=offset,
                with_payload=True,
            )
            for record in page:
                payload = record.payload or {}
                by_doc.setdefault(payload.get("document_name", "?"), []).append(payload)
            if offset is None:
                break
    except Exception as exc:  # noqa: BLE001
        print(f"  (could not read the index: {exc})")
    return by_doc


async def _indexed_crops(settings) -> dict[str, int]:
    """role=table_image counts from the image collection, keyed by document."""
    if not settings.multimodal.enabled:
        return {}
    try:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        from gernas_rag.embeddings.multimodal.factory import get_multimodal_embedder
        from gernas_rag.vectordb.image_factory import get_image_store

        vectordb = get_vectordb(settings.vectordb)
        space = get_multimodal_embedder(settings.multimodal.embedding).space
        collection = settings.multimodal.image_collection_name or space.collection_name(
            settings.multimodal.image_collection_base
        )
        store = get_image_store(settings.vectordb, collection, vectordb)
        page, _ = await store._client.scroll(  # noqa: SLF001
            collection_name=collection,
            scroll_filter=Filter(
                must=[FieldCondition(key="role", match=MatchValue(value="table_image"))]
            ),
            limit=512,
            with_payload=True,
        )
        counts: dict[str, int] = {}
        for record in page:
            doc = (record.payload or {}).get("document_name", "?")
            counts[doc] = counts.get(doc, 0) + 1
        return counts
    except Exception as exc:  # noqa: BLE001
        print(f"  (no image collection yet: {exc})")
        return {}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", default="./docs")
    parser.add_argument("--show-tables", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    chunker = get_chunker(settings.chunking)
    metadata = MetadataExtractor()

    print(f"protect_tables      : {settings.chunking.protect_tables}")
    print(f"extraction_strategy : {settings.chunking.extraction_strategy.value}")
    print(f"multimodal.enabled  : {settings.multimodal.enabled}")
    print(f"extract_table_crops : {settings.multimodal.extraction.extract_table_crops}")
    if not settings.chunking.protect_tables:
        print("\nWARNING: protect_tables is FALSE — tables are not atomic.")

    indexed = await _indexed_tables(settings)
    crops = await _indexed_crops(settings)

    root = Path(args.path)
    files = (
        [root]
        if root.is_file()
        else sorted(
            f
            for ext in settings.ingestion.supported_extensions
            for f in root.glob(f"**/*{ext}")
        )
    )

    print(
        f"\n{'Document':<46} {'src':>4} {'chunk':>6} {'idx':>4} {'crop':>5} "
        f"{'rows s/c':>10}  verdict"
    )
    print("-" * 100)

    problems: list[str] = []
    for pdf in files:
        try:
            extraction = await get_extractor(settings.chunking, pdf).extract(pdf)
        except Exception as exc:  # noqa: BLE001
            print(f"{pdf.name[:46]:<46} {'-':>4} {'-':>6} {'-':>4} {'-':>5} "
                  f"{'-':>10}  EXTRACT FAILED: {str(exc)[:28]}")
            continue

        # Ground truth: pipe-table blocks in the extracted markdown.
        source_tables = [m.group(0) for m in _TABLE_BLOCK.finditer(extraction.raw_markdown)]
        source_rows = sum(_body_rows(t) for t in source_tables)

        base = metadata.build_base_metadata(
            pdf, "", None, "", raw_text=extraction.raw_markdown
        )
        chunks = chunker.chunk(extraction, base)
        table_chunks = [c for c in chunks if c.metadata.content_type == "table"]
        chunk_rows = sum(c.metadata.table_rows or 0 for c in table_chunks)

        doc_name = base["document_name"]
        idx = indexed.get(doc_name, [])
        crop_count = crops.get(doc_name, 0)

        verdict = []
        # (a) every source table produced at least one chunk
        if source_tables and not table_chunks:
            verdict.append("TABLES LOST")
            problems.append(f"{pdf.name}: {len(source_tables)} source tables, 0 chunks")
        # (b) no rows dropped in chunking
        if source_rows and chunk_rows < source_rows:
            verdict.append(f"ROWS LOST {source_rows - chunk_rows}")
            problems.append(f"{pdf.name}: {source_rows - chunk_rows} rows dropped")
        # (c) every chunk carries a header
        for c in table_chunks:
            if not _DELIM.search(c.text):
                verdict.append("HEADERLESS")
                problems.append(f"{pdf.name}: chunk without delimiter row")
                break
        # (d) index reconciles with chunking. Drift means the INDEXED content
        # differs from what re-extraction produces now — usually a partial or
        # non-deterministic ingest (e.g. Docling std::bad_alloc), so the
        # document's table coverage cannot be trusted.
        if idx and len(idx) != len(table_chunks):
            verdict.append(f"INDEX DRIFT {len(idx)}!={len(table_chunks)}")
            problems.append(
                f"{pdf.name}: index has {len(idx)} table chunks but re-extraction "
                f"produces {len(table_chunks)} — re-ingest this document"
            )
        if not idx and table_chunks:
            verdict.append("NOT INDEXED")  # informational: ingest not run yet

        print(
            f"{pdf.name[:46]:<46} {len(source_tables):>4} {len(table_chunks):>6} "
            f"{len(idx):>4} {crop_count:>5} {f'{source_rows}/{chunk_rows}':>10}  "
            f"{', '.join(verdict) if verdict else 'OK'}"
        )

        if args.show_tables:
            for c in table_chunks:
                head = c.text.split("\n")[0][:70]
                print(f"      part={c.metadata.table_part or '1/1'} "
                      f"rows={c.metadata.table_rows} asset={c.metadata.asset_id or '-'} "
                      f"| {head}")

    print("-" * 100)
    print("src=tables in source markdown  chunk=atomic table chunks  "
          "idx=indexed  crop=image vectors  rows s/c=source/chunked")

    print("\nVECTOR PLACEMENT")
    total_idx = sum(len(v) for v in indexed.values())
    total_crop = sum(crops.values())
    print(f"  TEXT  vectors (BGE-M3 dense+sparse, content_type=table): {total_idx}")
    print(f"  IMAGE vectors (SigLIP-2 dense, role=table_image)       : {total_crop}")
    if total_idx and not total_crop:
        print("  -> Tables are TEXT-ONLY right now. That is fully functional for")
        print("     retrieval; the crop only adds layout fidelity for the vision LLM.")
        print("     Enable with: RAG__MULTIMODAL__EXTRACTION__BACKEND=docling")

    print(f"\n{'PASS: no problems detected' if not problems else 'PROBLEMS:'}")
    for p in problems:
        print(f"  - {p}")
    if problems:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
