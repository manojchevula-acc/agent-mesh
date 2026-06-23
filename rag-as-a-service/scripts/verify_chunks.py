"""Verify parent-child relationships and flag anomalies in stored chunks."""

import asyncio
import re
import sys

sys.path.insert(0, "src")

from qdrant_client import AsyncQdrantClient


async def main():
    client = AsyncQdrantClient(path="./qdrant_storage")
    results = await client.scroll(
        collection_name="fab_gernas_docs", limit=200, with_payload=True, with_vectors=False
    )
    points = results[0]
    all_payloads = [p.payload or {} for p in points]

    by_chunk_id = {pay["chunk_id"]: pay for pay in all_payloads if "chunk_id" in pay}
    parents = {cid: pay for cid, pay in by_chunk_id.items() if pay.get("is_parent")}
    children = [pay for pay in all_payloads if not pay.get("is_parent")]

    # ── Parent-child tree ─────────────────────────────────────────────
    print("=== PARENT-CHILD RELATIONSHIP CHECK ===")
    all_docs = sorted({pay.get("document_name", "") for pay in all_payloads})
    for doc in all_docs:
        doc_parents = [v for v in parents.values() if v.get("document_name") == doc]
        doc_children = [v for v in children if v.get("document_name") == doc]
        print(f"\n{doc}  ({len(doc_parents)} parents, {len(doc_children)} children)")
        for par in sorted(doc_parents, key=lambda x: x.get("clause_reference", "")):
            par_id = par.get("chunk_id")
            my_children = [c for c in doc_children if c.get("parent_chunk_id") == par_id]
            pchars = len(par.get("text", ""))
            cchars = sum(len(c.get("text", "")) for c in my_children)
            print(
                f"  PARENT [{par.get('clause_reference'):12}] {pchars:5} chars"
                f"  ->  {len(my_children)} children  ({cchars} chars combined)"
            )
            for c in sorted(my_children, key=lambda x: x.get("clause_reference", "")):
                print(f"    CHILD  [{c.get('clause_reference'):12}] {len(c.get('text', '')):5} chars")

        # Children with no parent
        orphans = [c for c in doc_children if c.get("parent_chunk_id") not in by_chunk_id]
        if orphans:
            for o in orphans:
                print(f"  ORPHAN [{o.get('clause_reference'):12}] {len(o.get('text', '')):5} chars  (parent_chunk_id not in store)")

    # ── Anomaly checks ────────────────────────────────────────────────
    print("\n=== ANOMALY CHECKS ===")
    issues = []

    for pay in all_payloads:
        text_len = len(pay.get("text", ""))
        clause = pay.get("clause_reference", "")
        doc = pay.get("document_name", "")[:40]
        is_parent = pay.get("is_parent", False)

        if not is_parent and text_len < 200:
            issues.append(f"TINY CHILD      [{doc}]  clause={clause!r}  chars={text_len}  (below 200)")

        if re.match(r"^\d+\.\d{2,}$", clause):
            issues.append(f"SUSPECT CLAUSE  [{doc}]  clause={clause!r}  (looks like price/pct, not clause number)")

        if is_parent and text_len < 500:
            issues.append(f"TINY PARENT     [{doc}]  clause={clause!r}  chars={text_len}  (smaller than some children)")

    docs_missing_date = sorted({
        pay.get("document_name", "") for pay in all_payloads
        if not pay.get("is_parent") and not pay.get("effective_date")
    })
    for d in docs_missing_date:
        issues.append(f"NO DATE         [{d[:40]}]  (effective_date empty — not found in doc text)")

    if issues:
        for issue in issues:
            print(f"  WARN  {issue}")
    else:
        print("  All checks passed.")

    # ── Quick stats ───────────────────────────────────────────────────
    print("\n=== STATS ===")
    child_sizes = [len(pay.get("text", "")) for pay in children]
    parent_sizes = [len(pay.get("text", "")) for pay in parents.values()]
    print(f"  Children : {len(child_sizes):3}  |  min={min(child_sizes)}  avg={sum(child_sizes)//len(child_sizes)}  max={max(child_sizes)} chars")
    print(f"  Parents  : {len(parent_sizes):3}  |  min={min(parent_sizes)}  avg={sum(parent_sizes)//len(parent_sizes)}  max={max(parent_sizes)} chars")


if __name__ == "__main__":
    asyncio.run(main())
