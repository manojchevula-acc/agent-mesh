# Semantic Response Cache — Implementation Overview

AgentMesh uses a **semantic vector cache** backed by ChromaDB to short-circuit the expensive LLM pipeline when a semantically similar question has already been answered for the same role. A full pipeline run costs 2–4 A2A LLM round-trips (~5–70 s). A cache hit costs one embedding call (~50 ms).

---

## How It Works

```
User Query
    │
    ▼
InputGuardrail ──► RBACValidation ──► CacheCheckExecutor
                                             │
                          ┌──────────────────┴──────────────────┐
                          │ HIT                                  │ MISS
                          ▼                                      ▼
                   yield cached answer               ComplianceExecutor
                   (replay reasoning)                      │
                                                    DomainExecutor (LLM)
                                                           │
                                                    store answer + reasoning
                                                    in ChromaDB
```

On a **HIT**: the answer, AI reasoning, and provenance metadata are served directly from ChromaDB — Compliance and Domain LLM calls are skipped entirely.

On a **MISS**: the full pipeline runs, and the final (post-redaction) answer is stored in ChromaDB alongside the LLM reasoning for future hits.

---

## Pipeline Position

`CacheCheckExecutor` sits **after RBAC, before Compliance**.

- RBAC must resolve first so the role is known — cache is role-scoped (a `relationship_manager` cannot hit a `credit_officer` entry).
- Compliance and Domain are skipped on HIT — this is where all LLM cost is saved.
- HITL resume workflows are explicitly excluded from caching — approved requests always run fresh.

---

## Storage: ChromaDB

| Property | Value |
|---|---|
| Backend | ChromaDB `PersistentClient` (SQLite on-disk) |
| Location | `data/cache/chroma/` (configurable via `CACHE_CHROMA_DIR`) |
| Collection | `mesh_response_cache` (configurable via `CACHE_COLLECTION_NAME`) |
| Distance metric | Cosine (`hnsw:space=cosine`) — **must be set on collection creation** |
| Embedding model | `all-MiniLM-L6-v2` via ChromaDB `DefaultEmbeddingFunction` (onnxruntime, no HuggingFace download) |
| Vector dimensions | 384 |

### Per-document metadata schema

| Field | Type | Purpose |
|---|---|---|
| `role` | string | Role isolation — lookup filters `where={"role": {"$eq": role}}` |
| `answer` | string | Stored answer (capped at 8192 chars) |
| `route` | string | Domain route that produced the answer (Data Layer / RAG / Hybrid) |
| `session_id` | string | Source session for audit trace-back |
| `request_id` | string | Source request ID for audit trace-back |
| `ts_iso` | string | ISO timestamp when stored |
| `ts_unix` | float | Unix timestamp (used for fast age expiry check) |
| `reasoning` | string | JSON-serialized LLM reasoning entries (capped at 8192 chars) |

Document IDs are **deterministic**: `uuid5(sha256(role + "::" + query))` — this makes `upsert` idempotent; re-asking the same question never creates duplicate entries.

---

## Lookup Logic (`SemanticCacheStore.lookup`)

```
1. Embed query → 384-dim float vector
2. ChromaDB cosine query, filtered by role, n_results=1
3. similarity = 1.0 - distance
4. If similarity < CACHE_SIMILARITY_THRESHOLD → MISS
5. age_hours = (now - ts_unix) / 3600
6. If age_hours > CACHE_MAX_AGE_HOURS → MISS
7. Deserialize reasoning JSON from metadata
8. Return CacheEntry(answer, similarity, age_hours, reasoning, ...)
```

All exceptions are caught — any ChromaDB or embedding failure degrades gracefully to a MISS so the full pipeline runs as a fallback.

---

## Store Logic (`SemanticCacheStore.store`)

Called by the orchestrator **after** the final answer is produced (post-PII-redaction, post-HITL).

- No-op if `ENABLE_RESPONSE_CACHE=false`
- No-op if the answer is itself a cache hit (`cache_hit=True`) — prevents re-caching stale data
- Reasoning is serialized to JSON; if it exceeds 8192 chars, entries are truncated (whole entries, not mid-JSON)
- Serialization errors are logged as warnings but do **not** prevent the upsert — the entry is stored with `reasoning=[]`
- Write operations are serialized via `threading.Lock` — ChromaDB's SQLite backend is not safe for concurrent writes

---

## Role Isolation

Every lookup and store operation includes the resolved `role` value (e.g. `relationship_manager`).

ChromaDB filters: `where={"role": {"$eq": role}}`

**Effect**: A `relationship_manager` asking "Show customer profile for CUST001" will never receive a cached answer that was produced for a `credit_officer`, even if the answer text is identical. Each role gets its own isolated cache space.

---

## LLM Reasoning Replay

When the cache was originally populated, the LLM reasoning entries (`<llm_reasoning>` blocks extracted from agent responses) were serialized and stored alongside the answer.

On a cache HIT:
1. Reasoning entries are deserialized from ChromaDB metadata
2. Injected into the active execution tracer → populates **AI Reasoning tab** in the UI
3. Emitted as an SSE `reasoning` event with `replayed_from_cache: true` → live streaming works too
4. `llm_reasoning` field in the API response is populated from `cache_reasoning` (fallback)
5. The AI Reasoning tab shows a small **"replayed"** italic label to indicate the reasoning is from the original run

Old cache entries (created before reasoning storage was added) return `reasoning=[]` gracefully — the UI reasoning tab will be empty for those entries only.

---

## Configuration

All settings live in `agent-mesh/.env` and are read by `src/config.py` at startup.

| Variable | Default | Description |
|---|---|---|
| `ENABLE_RESPONSE_CACHE` | `false` | Master switch — must be `true` to activate |
| `CACHE_SIMILARITY_THRESHOLD` | `0.92` | Minimum cosine similarity to accept a hit (0.0–1.0) |
| `CACHE_MAX_AGE_HOURS` | `24.0` | Maximum age of a cached entry in hours |
| `CACHE_CHROMA_DIR` | `data/cache/chroma` | On-disk path for ChromaDB persistent storage |
| `CACHE_EMBED_MODEL` | `all-MiniLM-L6-v2` | Label only — model is always `DefaultEmbeddingFunction` |
| `CACHE_COLLECTION_NAME` | `mesh_response_cache` | ChromaDB collection name |

### Threshold tuning guide

| Threshold | Behaviour |
|---|---|
| `0.99+` | Only exact/near-exact phrase matches hit. Paraphrases miss. |
| `0.92` (default) | Identical and very close paraphrases hit. Different intent misses. |
| `0.85` | Broader paraphrases hit. Risk of semantic drift (wrong answer served). |
| `< 0.80` | Not recommended — too many false positives for a banking context. |

---

## Cache Hit — UI Indicators

| Location | Indicator |
|---|---|
| Message bubble | Amber banner: `⚡ Served from semantic cache · Xh ago · X% match` |
| Pipeline steps | `Cache Check` step shows amber border + ⚡ icon with `HIT` result |
| AI Reasoning tab | Reasoning entries shown with italic `replayed` label next to tab name |
| Execution Summary | `total_duration_ms` is the cache lookup time (~50 ms), not the original LLM time |

---

## Startup Indexer (`src/cache/cache_indexer.py`)

On `api_server.py` startup, `index_conversations_async()` runs as a background task:

1. Calls `_warmup()` — pre-loads the embedding model so the first real request pays no cold-start cost
2. Scans existing `data/conversations/*.jsonl` files
3. Pairs `user` → `assistant` message records and calls `store()` for each pair
4. Skips if the collection already has entries (prevents re-indexing on restart)

This means historical conversations are available as cache candidates from the first request after server start.

---

## Cache Stats API

```
GET /api/cache/stats
```

```json
{
  "enabled": true,
  "total_entries": 42,
  "similarity_threshold": 0.92,
  "max_age_hours": 24.0,
  "embed_model": "all-MiniLM-L6-v2",
  "chroma_dir": "data/cache/chroma",
  "collection_name": "mesh_response_cache"
}
```

---

## Key Files

| File | Role |
|---|---|
| `src/cache/semantic_cache.py` | `SemanticCacheStore` — lookup, store, embed, ChromaDB lifecycle |
| `src/cache/cache_indexer.py` | Startup warmup + historical JSONL indexer |
| `src/cache/__init__.py` | Package init, exports `get_cache_store()` singleton |
| `src/mesh/workflow.py` | `CacheCheckExecutor` — pipeline gate + reasoning replay |
| `src/mesh/orchestrator.py` | Calls `cache.store()` post-redaction; propagates cache fields to `MeshResult` |
| `src/config.py` | All `CACHE_*` config vars |
| `src/observability/metrics.py` | `record_cache(result, role, duration_ms)` — OTel counter + histogram |
| `api_server.py` | `/api/cache/stats` endpoint; startup indexer task; `llm_reasoning` fallback |

---

## Resetting the Cache

To clear all cached entries (e.g. after changing the similarity threshold or embedding model):

```powershell
Remove-Item -Recurse -Force agent-mesh\data\cache\chroma
```

Restart the API server. The collection will be recreated empty and repopulated from the next pipeline run.

> **Note:** Changing `CACHE_COLLECTION_NAME` without deleting the old directory leaves the old collection on disk but unused — it does not affect correctness.

---

## Known Limitations

| Limitation | Detail |
|---|---|
| Answer staleness | A cached answer does not reflect data changes (e.g. updated credit score). Use `CACHE_MAX_AGE_HOURS` to control freshness. |
| Reasoning truncation | Reasoning JSON is capped at 8192 chars. Very verbose reasoning runs may be partially stored — the truncation removes whole entries (never cuts mid-JSON). |
| Single-node only | ChromaDB PersistentClient uses SQLite — not suitable for multi-process deployments without a shared volume. Use `chromadb.HttpClient` for distributed deployments. |
| Cold-start (first process boot) | The embedding model loads lazily on first use (~1–3 s). The startup indexer pre-warms this to eliminate the cold-start from real requests. |
