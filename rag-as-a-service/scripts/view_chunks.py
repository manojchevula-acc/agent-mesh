"""Quick script to view chunks stored in Qdrant."""

import asyncio
import sys

sys.path.insert(0, "src")

from qdrant_client import AsyncQdrantClient


async def main():
    client = AsyncQdrantClient(path="./qdrant_storage")
    info = await client.get_collection("fab_gernas_docs")
    print(f"Total points (chunks): {info.points_count}")
    print()

    results = await client.scroll(
        collection_name="fab_gernas_docs",
        limit=200,
        with_payload=True,
        with_vectors=False,
    )
    points = results[0]

    # Summary by document
    from collections import defaultdict
    by_doc = defaultdict(lambda: {"parent": 0, "child": 0})
    for p in points:
        pay = p.payload or {}
        doc = pay.get("document_name", "unknown")
        if pay.get("is_parent"):
            by_doc[doc]["parent"] += 1
        else:
            by_doc[doc]["child"] += 1

    print("=== Summary by document ===")
    print(f"{'Document':<50} {'Parents':>8} {'Children':>9} {'Total':>7}")
    print("-" * 78)
    for doc, counts in sorted(by_doc.items()):
        total = counts["parent"] + counts["child"]
        print(f"{doc[:49]:<50} {counts['parent']:>8} {counts['child']:>9} {total:>7}")

    print()
    print("=== All chunks ===")
    print(f"{'#':<5} {'document_name':<45} {'type':<18} {'parent':<8} {'clause':<15} {'chars'}")
    print("-" * 100)
    for i, p in enumerate(points, 1):
        pay = p.payload or {}
        print(
            f"{i:<5} "
            f"{pay.get('document_name', '')[:44]:<45} "
            f"{pay.get('document_type', ''):<18} "
            f"{'YES' if pay.get('is_parent') else 'no':<8} "
            f"{pay.get('clause_reference', '')[:14]:<15} "
            f"{len(pay.get('text', ''))}"
        )

    # Show full text of first child chunk as sample
    print()
    print("=== Sample chunk text (first child chunk) ===")
    for p in points:
        pay = p.payload or {}
        if not pay.get("is_parent"):
            print(f"Document : {pay.get('document_name')}")
            print(f"Type     : {pay.get('document_type')}")
            print(f"Clause   : {pay.get('clause_reference')}")
            print(f"Date     : {pay.get('effective_date')}")
            print(f"Text     :\n{pay.get('text', '')[:600]}")
            break


if __name__ == "__main__":
    asyncio.run(main())
