"""Startup JSONL indexer — pre-populates the semantic cache from existing conversations.

Run once at api_server startup via asyncio.to_thread so it never blocks the event loop.

Behaviour
---------
- Skips entirely if ENABLE_RESPONSE_CACHE=false.
- Skips if the ChromaDB collection already has entries (prevents re-indexing every restart).
- Scans all *.jsonl files in Config.CONVERSATION_STORE_DIR.
- Pairs consecutive role=user → role=assistant records and stores each pair.
- Runs _warmup() first so the embedding model is loaded before the first real request.
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import time
from datetime import datetime, timezone

from src.config import Config

_log = logging.getLogger("agent_mesh.cache.indexer")


async def index_conversations_async() -> None:
    """Async entry point: delegates blocking work to a thread pool."""
    if not Config.ENABLE_RESPONSE_CACHE:
        return
    await asyncio.to_thread(_index_conversations_sync)


def _index_conversations_sync() -> None:
    """Blocking worker. Runs inside asyncio.to_thread."""
    from src.cache.semantic_cache import get_cache_store

    store = get_cache_store()
    store._warmup()  # pre-load model + open ChromaDB before any real requests arrive

    # Skip if collection already populated (idempotent restarts)
    try:
        if store._collection and store._collection.count() > 0:
            _log.info(
                "cache indexer: collection already has %d entries — skipping re-index",
                store._collection.count(),
            )
            return
    except Exception:
        pass

    conv_dir = pathlib.Path(Config.CONVERSATION_STORE_DIR)
    if not conv_dir.exists():
        _log.info("cache indexer: conversation dir %s not found — nothing to index", conv_dir)
        return

    indexed = errors = 0
    t0 = time.perf_counter()
    for jsonl_path in sorted(conv_dir.glob("*.jsonl")):
        try:
            _index_session_file(store, jsonl_path)
            indexed += 1
        except Exception as exc:
            _log.warning("cache indexer: failed to index %s: %s", jsonl_path.name, exc)
            errors += 1

    elapsed_ms = (time.perf_counter() - t0) * 1000
    _log.info(
        "cache indexer: done sessions=%d errors=%d elapsed_ms=%.0f",
        indexed, errors, elapsed_ms,
    )


def _index_session_file(store, path: pathlib.Path) -> None:
    """Parse one JSONL session file and store all valid Q/A turn pairs."""
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Only keep user/assistant turn records; skip rolling_summary-only records
            if isinstance(rec, dict) and rec.get("role") in ("user", "assistant"):
                records.append(rec)

    # Pair consecutive user → assistant turns
    i = 0
    while i < len(records) - 1:
        user_rec = records[i]
        asst_rec = records[i + 1]
        if user_rec.get("role") == "user" and asst_rec.get("role") == "assistant":
            query = (user_rec.get("content") or "").strip()
            answer = (asst_rec.get("content") or "").strip()

            # role_at_time is added by orchestrator going forward; fall back to
            # a heuristic from the session filename (user_<uuid> prefix pattern)
            role = asst_rec.get("role_at_time") or _infer_role_from_filename(path.stem)
            route = asst_rec.get("route") or "unknown"
            session_id = path.stem
            request_id = asst_rec.get("request_id") or ""
            ts_str = asst_rec.get("ts") or ""
            try:
                ts = datetime.fromisoformat(ts_str) if ts_str else datetime.now(timezone.utc)
            except ValueError:
                ts = datetime.now(timezone.utc)

            if query and answer and role:
                store.store(
                    query=query,
                    answer=answer,
                    role=role,
                    route=route,
                    session_id=session_id,
                    request_id=request_id,
                    ts=ts,
                )
            i += 2
        else:
            i += 1


def _infer_role_from_filename(stem: str) -> str:
    """Best-effort role inference from session filename (e.g. 'alice_abc123').

    Returns empty string if the username cannot be resolved — such entries will
    be stored with role='' and never matched (the lookup filters by exact role).
    Forward-looking sessions have role_at_time set by orchestrator.py.
    """
    try:
        from src.auth.identity_provider import login
        # session filenames are typically "{username}_{uuid_fragment}"
        username = stem.rsplit("_", 1)[0] if "_" in stem else stem
        user = login(username)
        return user.role.value if user else ""
    except Exception:
        return ""
