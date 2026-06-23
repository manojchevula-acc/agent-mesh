"""CLI script to ingest documents from a directory or a single file.

Usage:
    python scripts/ingest_docs.py --path ./docs --document-type pricing_policy
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, "src")

from gernas_rag.config.settings import get_settings  # noqa: E402
from gernas_rag.embeddings.factory import get_embedder  # noqa: E402
from gernas_rag.ingestion.pipeline import IngestionPipeline  # noqa: E402
from gernas_rag.vectordb.factory import get_vectordb  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True)
    parser.add_argument("--document-type", default="")  # empty = auto-infer from filename
    parser.add_argument("--product-applicability", default="")
    parser.add_argument("--effective-date", default="")
    args = parser.parse_args()

    settings = get_settings()
    embedder = get_embedder(settings.embedding)
    vectordb = get_vectordb(settings.vectordb)
    await vectordb.create_collection(settings.vectordb.collection_name, embedder.dense_dim)

    pipeline = IngestionPipeline(settings, embedder, vectordb)
    doc_path = Path(args.path)

    if doc_path.is_file():
        result = await pipeline.ingest_file(
            doc_path,
            args.document_type,
            [p.strip() for p in args.product_applicability.split(",") if p.strip()],
            args.effective_date,
        )
        print(f"Ingested: {result.chunks_created} chunks — {result.status}")
    else:
        results = await pipeline.ingest_directory(doc_path, args.document_type)
        total = sum(r.chunks_created for r in results)
        print(f"Ingested {len(results)} documents · {total} total chunks")


if __name__ == "__main__":
    asyncio.run(main())
