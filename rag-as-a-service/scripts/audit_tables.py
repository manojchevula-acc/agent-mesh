"""D8 corpus gate — no indexed table chunk may lack a header row.

Scans every ``content_type=table`` point in the text collection and asserts:
  1. a markdown delimiter row (|---|) is present, and
  2. for row-split tables, every part starts with the same header line.

Needs no LLM and no model; runs in seconds. ``--strict`` exits non-zero on any
violation so it can gate CI.

Usage:
    python scripts/audit_tables.py
    python scripts/audit_tables.py --strict
"""

import argparse
import asyncio
import re
import sys
from collections import defaultdict

sys.path.insert(0, "src")

from gernas_rag.config.settings import get_settings  # noqa: E402
from gernas_rag.vectordb.factory import get_vectordb  # noqa: E402

_DELIM_RE = re.compile(r"^\s*\|[\s:\-|]+\|\s*$", re.MULTILINE)


async def _scan(limit: int) -> tuple[list[dict], dict]:
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    settings = get_settings()
    vectordb = get_vectordb(settings.vectordb)
    client = vectordb._client  # noqa: SLF001 - diagnostic script

    records: list[dict] = []
    offset = None
    while True:
        page, offset = await client.scroll(
            collection_name=settings.vectordb.collection_name,
            scroll_filter=Filter(
                must=[FieldCondition(key="content_type", match=MatchValue(value="table"))]
            ),
            limit=min(256, limit - len(records)) or 1,
            offset=offset,
            with_payload=True,
        )
        records.extend(r.payload for r in page if r.payload)
        if offset is None or len(records) >= limit:
            break

    stats = {
        "tables_scanned": len(records),
        "documents": len({r.get("document_name", "?") for r in records}),
    }
    return records, stats


def _check(records: list[dict]) -> list[str]:
    violations: list[str] = []
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)

    for payload in records:
        text = payload.get("text", "")
        label = (
            f"{payload.get('document_name', '?')} / "
            f"{payload.get('clause_reference', '?')} "
            f"(part {payload.get('table_part') or '1/1'})"
        )
        if not _DELIM_RE.search(text):
            violations.append(f"MISSING HEADER: {label}")
        part = payload.get("table_part")
        if part:
            key = (payload.get("document_name", "?"), payload.get("clause_reference", "?"))
            groups[key].append(payload)

    # Header repetition: every part of a split table starts with the same header.
    for key, parts in groups.items():
        headers = set()
        for payload in parts:
            lines = [
                ln for ln in payload.get("text", "").split("\n") if ln.strip().startswith("|")
            ]
            if lines:
                headers.add(lines[0].strip())
        if len(headers) > 1:
            violations.append(f"HEADER MISMATCH across parts: {key[0]} / {key[1]}")

    return violations


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true", help="exit 1 on any violation")
    parser.add_argument("--limit", type=int, default=10_000)
    args = parser.parse_args()

    records, stats = await _scan(args.limit)
    violations = _check(records)

    print(f"Tables scanned : {stats['tables_scanned']}")
    print(f"Documents      : {stats['documents']}")
    print(f"Violations     : {len(violations)}")
    for v in violations[:50]:
        print(f"  - {v}")
    if len(violations) > 50:
        print(f"  ... and {len(violations) - 50} more")

    if not records:
        print(
            "\nNo table chunks found. Either the corpus has no tables, or it was "
            "ingested before chunking.protect_tables was enabled (reindex needed)."
        )
    elif not violations:
        print("\nPASS: every indexed table chunk carries a header row.")

    if args.strict and violations:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
