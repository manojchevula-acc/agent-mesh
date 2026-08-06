"""CLI script to create the vector DB collection + payload indexes.

Usage:
    python scripts/setup_vectordb.py
"""

import asyncio
import sys

sys.path.insert(0, "src")

from gernas_rag.config.settings import get_settings  # noqa: E402
from gernas_rag.embeddings.factory import get_embedder  # noqa: E402
from gernas_rag.vectordb.factory import get_vectordb  # noqa: E402


async def main() -> None:
    settings = get_settings()
    embedder = get_embedder(settings.embedding)
    vectordb = get_vectordb(settings.vectordb)
    name = settings.vectordb.collection_name

    await vectordb.create_collection(name, embedder.dense_dim)
    healthy = await vectordb.health_check()
    print(f"Collection '{name}' ready · provider={settings.vectordb.provider} · healthy={healthy}")

    if not settings.multimodal.enabled:
        print("multimodal.enabled=false — no image collection created.")
        return

    from gernas_rag.embeddings.multimodal.factory import get_multimodal_embedder
    from gernas_rag.vectordb.image_factory import get_image_store

    mm_embedder = get_multimodal_embedder(settings.multimodal.embedding)
    space = mm_embedder.space  # forces a load so the dim is probed, not assumed
    collection = settings.multimodal.image_collection_name or space.collection_name(
        settings.multimodal.image_collection_base
    )
    image_store = get_image_store(settings.vectordb, collection, vectordb)
    await image_store.create_collection(collection, space.dim, space.metric)

    print(f"Image collection '{collection}' ready")
    print(f"  model    : {space.model_name}")
    print(f"  dim      : {space.dim}")
    print(f"  space_id : {space.space_id}")
    print(f"  images   : {await image_store.count()}")


if __name__ == "__main__":
    asyncio.run(main())
