"""Startup cache warmup — pre-loads the embedding model and opens ChromaDB.

Previously this module also JSONL-indexed conversation files at startup, which
could block startup on large histories. JSONL indexing is now delegated to the
ingest pipeline (src.cache.ingest_pipeline) which the operator runs manually
or via POST /api/cache/ingest.

This module now only performs _warmup() so the first real request pays no
cold-start embedding cost.
"""
from __future__ import annotations

import asyncio
import logging

from src.config import Config

_log = logging.getLogger("agent_mesh.cache.indexer")


async def index_conversations_async() -> None:
    """Async entry point: warms the embedding model + ChromaDB on startup.

    No longer indexes JSONL files — use the ingest pipeline instead:
        python -m src.cache.ingest_pipeline
    or
        POST /api/cache/ingest
    """
    if not Config.ENABLE_RESPONSE_CACHE:
        return
    await asyncio.to_thread(_warmup_sync)


def _warmup_sync() -> None:
    """Blocking warmup: pre-loads the embedding model and opens ChromaDB."""
    from src.cache.semantic_cache import get_cache_store

    store = get_cache_store()
    store._warmup()
    try:
        count = store._collection.count() if store._collection else 0
        _log.info("cache warmup: ready — %d entries in collection", count)
    except Exception:
        pass
