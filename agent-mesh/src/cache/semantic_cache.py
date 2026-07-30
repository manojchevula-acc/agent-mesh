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
    """A matched cache entry returned by SemanticCacheStore.lookup_with_id()."""
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
    # "high"          → similarity ≥ CACHE_SIMILARITY_THRESHOLD (definitive HIT, no judge needed)
    # "pending_judge" → similarity in gray zone (MISS_THRESHOLD ≤ sim < INTENT_MATCH_THRESHOLD);
    #                   CacheCheckExecutor will call llm_cache_judge
    # "intent_match"  → similarity in high-confidence suggestion zone (INTENT_MATCH_THRESHOLD ≤ sim < HIT_THRESHOLD);
    #                   CacheCheckExecutor surfaces root question to user (no judge needed)
    # "judge_hit"     → set by CacheCheckExecutor after judge returns YES
    confidence: str = "high"
    # ChromaDB document ID — populated by lookup_with_id(); used for accept/reject intent decisions
    entry_id: str = ""
    # Serialized entity signature (see src/cache/entity_extractor.signature_to_str);
    # "" when the entry predates entity gating (backfill or lookup-time extraction fills it).
    entities: str = ""
    # True when the "entities" metadata key was present on the stored entry (so entities=""
    # means a genuinely entity-free query). False for pre-gating entries — the gate then
    # extracts entities from query_original at lookup time.
    entities_indexed: bool = False


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

        # In-memory counters for LLM judge activity (reset on restart)
        self._judge_invocations: int = 0
        self._judge_hits: int = 0
        self._judge_misses: int = 0

        # In-memory counters for entity gate / reranker / accept-reject (reset on restart)
        self._entity_gate_drops: int = 0
        self._reranker_invocations: int = 0
        self._hit_accepted: int = 0
        self._hit_rejected: int = 0
        self._intent_accepted: int = 0
        self._intent_rejected: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def lookup(self, query: str, role: str) -> Optional[CacheEntry]:
        """Return a cached CacheEntry if a recent, role-matched, similar answer exists.

        Delegates to lookup_with_id(); entry_id is populated but callers may ignore it.
        Returns None on MISS or if the cache is disabled / unavailable.
        """
        return self.lookup_with_id(query, role)

    def lookup_with_id(self, query: str, role: str) -> Optional[CacheEntry]:
        """Return the single best-matching CacheEntry (delegates to lookup_top_n)."""
        results = self.lookup_top_n(query, role, n=1)
        return results[0] if results else None

    def lookup_top_n(self, query: str, role: str, n: int = 3) -> List[CacheEntry]:
        """Return up to n best-matching CacheEntries for query, sorted by similarity desc.

        Four-zone confidence per entry (when CACHE_INTENT_MATCH_ENABLED=true):
          similarity < CACHE_MISS_THRESHOLD                           → excluded (MISS)
          CACHE_MISS_THRESHOLD ≤ sim < CACHE_INTENT_MATCH_THRESHOLD   → "pending_judge"
          CACHE_INTENT_MATCH_THRESHOLD ≤ sim < CACHE_SIMILARITY_THRESHOLD → "intent_match"
          sim ≥ CACHE_SIMILARITY_THRESHOLD                             → "high"

        Stale entries (age > max_age_hours) and entries below CACHE_MISS_THRESHOLD are filtered out.
        Returns an empty list on MISS, disabled cache, or any exception.
        """
        if not self._enabled:
            return []
        try:
            self._ensure_initialized()
            count = self._collection.count()
            _log.info("cache lookup_top_n: collection has %d entries, role=%s, n=%d", count, role, n)
            if count == 0:
                return []
            vec = self._embed_query(query)
            # Hybrid retrieval fetches a wider dense candidate pool to re-rank with BM25.
            fetch_n = max(n, Config.CACHE_HYBRID_FETCH_K) if Config.CACHE_HYBRID_ENABLED else n
            results = self._collection.query(
                query_embeddings=[vec],
                n_results=min(fetch_n, count),
                where={"role": {"$eq": role}},
                include=["metadatas", "distances", "documents"],
            )
            ids = results.get("ids", [[]])[0]
            _log.info("cache lookup_top_n: query returned %d result(s)", len(ids))
            if not ids:
                return []

            miss_threshold = Config.CACHE_MISS_THRESHOLD
            intent_threshold = Config.CACHE_INTENT_MATCH_THRESHOLD
            intent_enabled = Config.CACHE_INTENT_MATCH_ENABLED

            entries: List[CacheEntry] = []
            for i, doc_id in enumerate(ids):
                distance = results["distances"][0][i]
                similarity = 1.0 - distance  # ChromaDB cosine space

                if similarity < miss_threshold:
                    continue  # below miss threshold — skip

                meta = results["metadatas"][0][i]
                age_hours = (time.time() - float(meta["ts_unix"])) / 3600.0
                if age_hours > self._max_age_hours:
                    continue  # stale

                doc = results["documents"][0][i] if results.get("documents") else ""
                try:
                    reasoning = json.loads(meta.get("reasoning", "[]") or "[]")
                    if not isinstance(reasoning, list):
                        reasoning = []
                except (json.JSONDecodeError, TypeError):
                    reasoning = []

                if similarity >= self._threshold:
                    confidence = "high"
                elif intent_enabled and similarity >= intent_threshold:
                    confidence = "intent_match"
                else:
                    confidence = "pending_judge"

                entries.append(CacheEntry(
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
                    confidence=confidence,
                    entry_id=doc_id,
                    entities=meta.get("entities", "") or "",
                    entities_indexed="entities" in meta,
                ))

            # Hybrid dense+sparse: fuse the dense order with a BM25 lexical order
            # (Reciprocal Rank Fusion) so rare discriminative tokens influence ranking.
            if Config.CACHE_HYBRID_ENABLED and len(entries) > 1:
                entries = self._hybrid_rerank(query, entries)

            entries = entries[:n]
            if entries:
                _log.info(
                    "cache lookup_top_n: returning %d entries, best sim=%.4f confidence=%s",
                    len(entries), entries[0].similarity, entries[0].confidence,
                )
            return entries  # sorted by similarity desc (dense) or fused rank (hybrid)
        except Exception as exc:
            _log.warning("cache lookup_top_n error: %s", exc, exc_info=True)
            return []

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
        entities: Optional[str] = None,
    ) -> None:
        """Persist a cache entry. No-op when disabled. Thread-safe via _write_lock.

        ``entities`` is a pre-computed, serialized entity signature (see
        src/cache/entity_extractor.signature_to_str). Callers compute it — store()
        stays synchronous and does no LLM work. Pass "" for a genuinely entity-free
        query; pass None only if the signature is unknown (the "entities" metadata
        key is then omitted and the lookup-time gate will extract on the fly).
        """
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
                vec = self._embed_query(query)
                ts_now = ts or datetime.now(timezone.utc)
                metadata = {
                    "role": role,
                    "answer": answer[:8192],  # cap to avoid SQLite blob size limits
                    "route": route,
                    "session_id": session_id,
                    "request_id": request_id,
                    "ts_iso": ts_now.isoformat(),
                    "ts_unix": float(ts_now.timestamp()),
                    "reasoning": reasoning_json,
                    "variant_count": 0,
                    "last_variant_ts": 0.0,
                }
                # Only write the entities key when a signature was provided, so a
                # missing key reliably means "not yet extracted" (see entities_indexed).
                if entities is not None:
                    metadata["entities"] = entities
                self._collection.upsert(
                    ids=[doc_id],
                    embeddings=[vec],
                    documents=[query],
                    metadatas=[metadata],
                )
                _log.info("cache store: upsert OK id=%s role=%s total=%d",
                          doc_id, role, self._collection.count())
        except Exception as exc:
            _log.warning("cache store error (traceback follows): %s", exc, exc_info=True)

    def increment_variant_count(self, entry_id: str) -> None:
        """Atomically increment variant_count and update last_variant_ts on a root entry.

        Called fire-and-forget (via asyncio.create_task + asyncio.to_thread) when
        a user accepts an intent suggestion — tracks how many times this root was reused.
        """
        if not self._enabled or not entry_id:
            return
        try:
            with self._write_lock:
                self._ensure_initialized()
                results = self._collection.get(
                    ids=[entry_id],
                    include=["metadatas", "documents", "embeddings"],
                )
                if not results or not results.get("ids"):
                    return
                meta = results["metadatas"][0].copy()
                meta["variant_count"] = int(meta.get("variant_count") or 0) + 1
                meta["last_variant_ts"] = float(time.time())
                # Update only the metadata — keep embedding + document unchanged
                self._collection.update(
                    ids=[entry_id],
                    metadatas=[meta],
                )
                _log.info(
                    "cache: incremented variant_count=%d for entry_id=%s",
                    meta["variant_count"], entry_id,
                )
        except Exception as exc:
            _log.warning("cache increment_variant_count error: %s", exc)

    def stats(self) -> dict:
        """Return lightweight cache statistics for the /api/cache/stats endpoint."""
        base = {
            "enabled": self._enabled,
            "similarity_threshold": self._threshold,
            "miss_threshold": Config.CACHE_MISS_THRESHOLD,
            "intent_match_enabled": Config.CACHE_INTENT_MATCH_ENABLED,
            "intent_match_threshold": Config.CACHE_INTENT_MATCH_THRESHOLD,
            "inline_store_enabled": Config.CACHE_INLINE_STORE_ENABLED,
            "judge_enabled": Config.CACHE_JUDGE_ENABLED,
            "judge_model": Config.CACHE_JUDGE_MODEL,
            "max_age_hours": self._max_age_hours,
            "embed_model": self._embed_model_name,
            "chroma_dir": self._chroma_dir,
            "collection_name": self._collection_name,
            "judge_invocations": self._judge_invocations,
            "judge_hits": self._judge_hits,
            "judge_misses": self._judge_misses,
            "entity_gate_drops": self._entity_gate_drops,
            "reranker_invocations": self._reranker_invocations,
            "hit_accepted": self._hit_accepted,
            "hit_rejected": self._hit_rejected,
            "intent_accepted": self._intent_accepted,
            "intent_rejected": self._intent_rejected,
            "reranker_enabled": Config.CACHE_RERANKER_ENABLED,
            "entity_gating_enabled": Config.CACHE_ENTITY_GATING_ENABLED,
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

    def _hybrid_rerank(self, query: str, entries: List["CacheEntry"]) -> List["CacheEntry"]:
        """Reorder dense candidates by fusing dense + BM25 rank (Reciprocal Rank Fusion).

        Sparse (BM25) is computed over the candidate documents so a rare token in
        the query (that the dense embedding under-weights) can lift the lexically
        matching candidate. RRF score = Σ 1/(k + rank). On any error (e.g. rank_bm25
        not installed) the original dense order is returned unchanged.
        """
        try:
            from rank_bm25 import BM25Okapi
        except Exception:
            _log.warning("hybrid retrieval: rank_bm25 not installed — keeping dense order")
            return entries
        try:
            docs = [(e.query_original or "").lower().split() for e in entries]
            if not any(docs):
                return entries
            bm25 = BM25Okapi(docs)
            sparse_scores = bm25.get_scores(query.lower().split())

            # entries are already in dense-desc order → dense rank = position.
            dense_rank = {id(e): r for r, e in enumerate(entries)}
            sparse_order = sorted(range(len(entries)), key=lambda i: sparse_scores[i], reverse=True)
            sparse_rank = {id(entries[i]): r for r, i in enumerate(sparse_order)}

            k = 60  # standard RRF constant
            def rrf(e):
                return 1.0 / (k + dense_rank[id(e)]) + 1.0 / (k + sparse_rank[id(e)])

            return sorted(entries, key=rrf, reverse=True)
        except Exception as exc:
            _log.warning("hybrid retrieval error (keeping dense order): %s", exc)
            return entries

    def _embed_query(self, query: str) -> list[float]:
        """Embed a query, applying canonicalization (Phase 2) when enabled.

        When CACHE_CANONICALIZE_ENABLED, structured-ID entities are replaced with
        placeholders before embedding so paraphrases of the same intent cluster
        tightly. The raw query is still what gets stored as the document and keys
        the doc ID — canonicalization only affects the embedded vector.
        """
        text = query
        if Config.CACHE_CANONICALIZE_ENABLED:
            from src.cache.entity_extractor import canonicalize_query
            text = canonicalize_query(query)
        return self._embed(text)

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
