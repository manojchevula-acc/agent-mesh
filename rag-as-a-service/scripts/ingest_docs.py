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
    parser.add_argument(
        "--with-images", action="store_true", help="force multimodal.enabled for this run"
    )
    args = parser.parse_args()

    if args.with_images:
        import os

        os.environ["RAG__MULTIMODAL__ENABLED"] = "true"
        get_settings.cache_clear()

    settings = get_settings()
    embedder = get_embedder(settings.embedding)
    vectordb = get_vectordb(settings.vectordb)
    await vectordb.create_collection(settings.vectordb.collection_name, embedder.dense_dim)

    image_pipeline = await _build_image_pipeline(settings, embedder, vectordb)
    pipeline = IngestionPipeline(settings, embedder, vectordb, image_pipeline)
    doc_path = Path(args.path)

    if doc_path.is_file():
        result = await pipeline.ingest_file(
            doc_path,
            args.document_type,
            [p.strip() for p in args.product_applicability.split(",") if p.strip()],
            args.effective_date,
        )
        print(
            f"Ingested: {result.chunks_created} text chunks "
            f"({result.tables_found} tables) · "
            f"{result.images_indexed} image vectors "
            f"({result.figures_indexed} figures + {result.table_crops_indexed} "
            f"table crops) — {result.status}"
        )
    else:
        results = await pipeline.ingest_directory(doc_path, args.document_type)
        total = sum(r.chunks_created for r in results)
        tables = sum(r.tables_found for r in results)
        figures = sum(r.figures_indexed for r in results)
        crops = sum(r.table_crops_indexed for r in results)
        print(
            f"Ingested {len(results)} documents · {total} text chunks "
            f"(of which {tables} are tables)"
        )
        print(
            f"Image vectors: {figures + crops} = {figures} figures "
            f"+ {crops} table crops"
        )
        if crops:
            print(
                "  (a table crop is a SECOND encoding of a table that already has "
                "a text chunk — not an extra document image)"
            )


async def _build_image_pipeline(settings, embedder, vectordb):
    """None when multimodal is disabled — the pipeline then behaves as before."""
    if not settings.multimodal.enabled:
        return None

    from gernas_rag.embeddings.multimodal.factory import get_multimodal_embedder
    from gernas_rag.images.store import get_asset_store
    from gernas_rag.ingestion.image_pipeline import ImageIngestionPipeline
    from gernas_rag.vectordb.image_factory import get_image_store

    mm_embedder = get_multimodal_embedder(settings.multimodal.embedding)
    space = mm_embedder.space
    collection = settings.multimodal.image_collection_name or space.collection_name(
        settings.multimodal.image_collection_base
    )
    image_store = get_image_store(settings.vectordb, collection, vectordb)
    await image_store.create_collection(collection, space.dim, space.metric)
    print(f"Multimodal: {space.model_name} (dim={space.dim}) -> {collection}")
    return ImageIngestionPipeline(
        settings,
        mm_embedder,
        embedder,
        image_store,
        vectordb,
        get_asset_store(settings.multimodal.storage),
    )


if __name__ == "__main__":
    asyncio.run(main())
