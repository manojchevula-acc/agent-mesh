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


if __name__ == "__main__":
    asyncio.run(main())
