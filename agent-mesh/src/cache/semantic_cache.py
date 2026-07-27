"""Semantic response cache backed by ChromaDB + sentence-transformers.

Stores Q&A pairs as dense vectors (384-dim, all-MiniLM-L6-v2) in a persistent
ChromaDB collection. Lookup finds the nearest prior answer for the same role
and returns it if it is recent enough and above the similarity threshold.

Thread safety
-------------
ChromaDB's Python client uses an in-process SQLite backend that is NOT safe for
concurrent writes.  All store() calls are serialised with _write_lock (a plain
threading.Lock so it works from both asyncio tasks dispatched via
asyncio.to_thread and the startup indexer thread).

Cold start
----------
The sentence-transformers model loads lazily on the first _embed() call (~1–3 s).
Call _warmup() at process start (done by cache_indexer) to push that cost out of
the first real request.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from src.config import Config

_log = logging.getLogger("agent_mesh.cache")


# ---------------------------------------------------------------------------
# Public dataclass returned on a cache hit
# ---------------------------------------------------------------------------

@dataclass
class CacheEntry:
    """A matched cache entry returned by SemanticCacheStore.lookup()."""
    query_original: str    # The stored query that matched (for tracing/debug)
    answer: str            # The stored redacted answer
    role: str              # Role the answer was produced for
    route: str             # Domain route (Data Layer / RAG / Hybrid)
    session_id: str        # Source session
    request_id: str        # Source request (for audit trace-back)
    ts_iso: str            # ISO timestamp when stored
    similarity: float      # Cosine similarity score [0, 1]
    age_hours: float       # Age of the entry in hours at lookup time
    reasoning: List[dict]  # LLM reasoning entries from the original pipeline run


# ---------------------------------------------------------------------------
# Module-level lazy singleton
# ---------------------------------------------------------------------------

_store_singleton: Optional["SemanticCacheStore"] = None
_store_singleton_lock = threading.Lock()


def get_cache_store() -> "SemanticCacheStore":
    """Return the process-wide SemanticCacheStore (thread-safe lazy init)."""
    global _store_singleton
    if _store_singleton is None:
        with _store_singleton_lock:
            if _store_singleton is None:
                _store_singleton = SemanticCacheStore()
    return _store_singleton


# ---------------------------------------------------------------------------
# SemanticCacheStore
# ---------------------------------------------------------------------------

class SemanticCacheStore:
    """Semantic response cache backed by ChromaDB + sentence-transformers."""

    def __init__(self) -> None:
        self._enabled = Config.ENABLE_RESPONSE_CACHE
        self._threshold = Config.CACHE_SIMILARITY_THRESHOLD
        self._max_age_hours = Config.CACHE_MAX_AGE_HOURS
        self._chroma_dir = Config.CACHE_CHROMA_DIR
        self._embed_model_name = Config.CACHE_EMBED_MODEL
        self._collection_name = Config.CACHE_COLLECTION_NAME

        self._collection = None   # lazy ChromaDB collection handle
        self._model = None        # lazy SentenceTransformer
        self._write_lock = threading.Lock()
        self._init_lock = threading.Lock()
        self._initialized = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def lookup(self, query: str, role: str) -> Optional[CacheEntry]:
        """Return a cached CacheEntry if a recent, role-matched, similar answer exists.

        Returns None on MISS or if the cache is disabled / unavailable.
        All exceptions are caught — the caller always gets a graceful None.
        """
        if not self._enabled:
            return None
        try:
            self._ensure_initialized()
            count = self._collection.count()
            _log.info("cache lookup: collection has %d entries, role=%s", count, role)
            if count == 0:
                return None
            vec = self._embed(query)
            results = self._collection.query(
                query_embeddings=[vec],
                n_results=min(1, count),
                where={"role": {"$eq": role}},
                include=["metadatas", "distances", "documents"],
            )
            ids = results.get("ids", [[]])[0]
            _log.info("cache lookup: query returned %d result(s)", len(ids))
            if not ids:
                return None

            distance = results["distances"][0][0]
            # ChromaDB cosine space: distance = 1 - cosine_similarity
            similarity = 1.0 - distance
            _log.info("cache lookup: best similarity=%.4f threshold=%.4f", similarity, self._threshold)
            if similarity < self._threshold:
                return None

            meta = results["metadatas"][0][0]
            age_hours = (time.time() - float(meta["ts_unix"])) / 3600.0
            _log.info("cache lookup: age_hours=%.2f max_age=%.1f", age_hours, self._max_age_hours)
            if age_hours > self._max_age_hours:
                return None

            doc = results["documents"][0][0] if results.get("documents") else ""
            try:
                reasoning = json.loads(meta.get("reasoning", "[]") or "[]")
                if not isinstance(reasoning, list):
                    reasoning = []
            except (json.JSONDecodeError, TypeError):
                reasoning = []
            return CacheEntry(
                query_original=doc,
                answer=meta.get("answer", ""),
                role=meta.get("role", role),
                route=meta.get("route", "unknown"),
                session_id=meta.get("session_id", ""),
                request_id=meta.get("request_id", ""),
                ts_iso=meta.get("ts_iso", ""),
                similarity=similarity,
                age_hours=age_hours,
                reasoning=reasoning,
            )
        except Exception as exc:
            _log.warning("cache lookup error (traceback follows): %s", exc, exc_info=True)
            return None

    def store(
        self,
        query: str,
        answer: str,
        role: str,
        route: str,
        session_id: str,
        request_id: str,
        ts: Optional[datetime] = None,
        reasoning: Optional[List[dict]] = None,
    ) -> None:
        """Persist a cache entry. No-op when disabled. Thread-safe via _write_lock."""
        if not self._enabled:
            return
        if not query or not answer:
            return
        # Serialize reasoning entries before acquiring the write lock.
        # Falls back to "[]" if entries contain non-serializable objects or
        # if the serialized form exceeds the 8192-byte metadata cap.
        reasoning_json = "[]"
        try:
            _raw = json.dumps(reasoning or [], ensure_ascii=False)
            if len(_raw) <= 8192:
                reasoning_json = _raw
            else:
                # Truncate to as many complete entries as fit.
                truncated: list = []
                for entry in (reasoning or []):
                    candidate = json.dumps(truncated + [entry], ensure_ascii=False)
                    if len(candidate) <= 8192:
                        truncated.append(entry)
                    else:
                        break
                reasoning_json = json.dumps(truncated, ensure_ascii=False)
        except (TypeError, ValueError) as _e:
            _log.warning("cache store: reasoning serialization failed (%s) — storing without reasoning", _e)
        try:
            with self._write_lock:
                self._ensure_initialized()
                doc_id = self._doc_id(role, query)
                vec = self._embed(query)
                ts_now = ts or datetime.now(timezone.utc)
                self._collection.upsert(
                    ids=[doc_id],
                    embeddings=[vec],
                    documents=[query],
                    metadatas=[{
                        "role": role,
                        "answer": answer[:8192],  # cap to avoid SQLite blob size limits
                        "route": route,
                        "session_id": session_id,
                        "request_id": request_id,
                        "ts_iso": ts_now.isoformat(),
                        "ts_unix": float(ts_now.timestamp()),
                        "reasoning": reasoning_json,
                    }],
                )
                _log.info("cache store: upsert OK id=%s role=%s total=%d",
                          doc_id, role, self._collection.count())
        except Exception as exc:
            _log.warning("cache store error (traceback follows): %s", exc, exc_info=True)

    def stats(self) -> dict:
        """Return lightweight cache statistics for the /api/cache/stats endpoint."""
        base = {
            "enabled": self._enabled,
            "similarity_threshold": self._threshold,
            "max_age_hours": self._max_age_hours,
            "embed_model": self._embed_model_name,
            "chroma_dir": self._chroma_dir,
            "collection_name": self._collection_name,
        }
        if not self._enabled:
            base["total_entries"] = 0
            return base
        try:
            self._ensure_initialized()
            base["total_entries"] = self._collection.count()
        except Exception as exc:
            base["total_entries"] = -1
            base["error"] = str(exc)
        return base

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_initialized(self) -> None:
        """Lazy-init ChromaDB client + collection (idempotent, thread-safe)."""
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            self._init_collection()
            self._initialized = True

    def _init_collection(self) -> None:
        import chromadb
        import pathlib
        pathlib.Path(self._chroma_dir).mkdir(parents=True, exist_ok=True)
        # Keep client alive on self — if it's a local var it gets GC'd and the
        # underlying SQLite connection closes, making self._collection unusable.
        self._client = chromadb.PersistentClient(path=self._chroma_dir)
        # hnsw:space=cosine is REQUIRED — without it ChromaDB defaults to L2 and
        # the similarity = 1 - distance formula gives wrong values.
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        _log.info(
            "cache: ChromaDB collection '%s' opened at %s (%d entries)",
            self._collection_name, self._chroma_dir, self._collection.count(),
        )

    def _embed(self, text: str) -> list[float]:
        """Return a 384-dim embedding for text. Lazy-loads the model on first call.

        Uses ChromaDB's built-in DefaultEmbeddingFunction (all-MiniLM-L6-v2 via
        onnxruntime, bundled with chromadb) — no HuggingFace download required.
        """
        if self._model is None:
            from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
            _log.info("cache: loading embedding model via chromadb DefaultEmbeddingFunction...")
            self._model = DefaultEmbeddingFunction()
            _log.info("cache: embedding model ready")
        return [float(x) for x in self._model([text])[0]]

    def _warmup(self) -> None:
        """Pre-load the embedding model and open the ChromaDB collection.

        Called from cache_indexer at startup so the first real request pays no
        cold-start cost.
        """
        if not self._enabled:
            return
        try:
            self._ensure_initialized()
            # Trigger model load with a dummy encode
            self._embed("warmup")
            _log.info("cache: warmup complete")
        except Exception as exc:
            _log.warning("cache warmup error: %s", exc)

    @staticmethod
    def _doc_id(role: str, query: str) -> str:
        """Deterministic, collision-resistant document ID (idempotent upserts)."""
        h = hashlib.sha256(f"{role}::{query}".encode()).hexdigest()
        return str(uuid.UUID(h[:32]))
