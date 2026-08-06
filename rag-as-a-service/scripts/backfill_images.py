"""Index images for already-ingested documents, without touching the text index.

This is what makes the multimodal rollout non-disruptive: the text collection is
read-only here, so a backfill can run against a live corpus.

Usage:
    python scripts/backfill_images.py --path ./docs
    python scripts/backfill_images.py --path ./docs --dry-run --limit 2
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, "src")

from gernas_rag.config.settings import get_settings  # noqa: E402
from gernas_rag.embeddings.factory import get_embedder  # noqa: E402
from gernas_rag.embeddings.multimodal.factory import get_multimodal_embedder  # noqa: E402
from gernas_rag.extraction.factory import get_extractor  # noqa: E402
from gernas_rag.chunking.factory import get_chunker  # noqa: E402
from gernas_rag.images.store import get_asset_store  # noqa: E402
from gernas_rag.ingestion.image_pipeline import ImageIngestionPipeline  # noqa: E402
from gernas_rag.ingestion.metadata import MetadataExtractor  # noqa: E402
from gernas_rag.vectordb.factory import get_vectordb  # noqa: E402
from gernas_rag.vectordb.image_factory import get_image_store  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", default="./docs")
    parser.add_argument("--document-type", default="")
    parser.add_argument("--limit", type=int, default=0, help="0 = no limit")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.multimodal.enabled:
        print("multimodal.enabled is false — set RAG__MULTIMODAL__ENABLED=true first.")
        sys.exit(1)

    text_embedder = get_embedder(settings.embedding)
    mm_embedder = get_multimodal_embedder(settings.multimodal.embedding)
    vectordb = get_vectordb(settings.vectordb)

    space = mm_embedder.space  # forces the load, so the dim is real
    collection = settings.multimodal.image_collection_name or space.collection_name(
        settings.multimodal.image_collection_base
    )
    image_store = get_image_store(settings.vectordb, collection, vectordb)
    await image_store.create_collection(collection, space.dim, space.metric)
    asset_store = get_asset_store(settings.multimodal.storage)

    print(f"Model      : {space.model_name} (dim={space.dim}, space={space.space_id})")
    print(f"Collection : {collection}")

    pipeline = ImageIngestionPipeline(
        settings, mm_embedder, text_embedder, image_store, vectordb, asset_store
    )
    extractor = get_extractor(settings.chunking, Path("placeholder.pdf"))
    chunker = get_chunker(settings.chunking)
    metadata = MetadataExtractor()

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
    if args.limit:
        files = files[: args.limit]

    total_images = 0
    for f in files:
        print(f"\n-> {f.name}")
        extraction = await extractor.extract(f)
        base_metadata = metadata.build_base_metadata(
            f, args.document_type, None, "", raw_text=extraction.raw_markdown
        )
        # Chunks are recomputed (not re-upserted) purely to resolve the
        # parent_chunk_id / table linkage for each asset.
        chunks = chunker.chunk(extraction, base_metadata)

        if args.dry_run:
            print("   dry-run: skipping embed + upsert")
            continue

        result = await pipeline.ingest_images(f, extraction, chunks, base_metadata)
        total_images += result.images_indexed
        print(
            f"   images={result.images_indexed} stubs={result.stubs_created} "
            f"table_crops={result.table_crops}"
        )
        for reason, count in sorted(result.stats.items()):
            if reason.startswith("rejected_"):
                print(f"     {reason}: {count}")

    print(f"\nBackfill complete: {total_images} images indexed across {len(files)} documents")


if __name__ == "__main__":
    asyncio.run(main())
