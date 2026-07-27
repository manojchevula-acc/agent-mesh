# Cross-Agent Collaboration Tools

PriceAssistAgent uses two MAF `@tool` functions to delegate sub-queries to specialist agents over A2A. These tools implement several safeguards to prevent runaway loops, duplicate calls, and silent failures.

---

## File

`src/tools/collaboration_tools.py`

---

## The Two Tools

### `query_structured_data(question: str) -> str`
Delegates to DataAgent (port 8016) over A2A.  
Used when PriceAssistAgent classifies intent as `data` or `hybrid`.

### `query_knowledge_base(question: str) -> str`
Delegates to RAGAgent (port 8017) over A2A.  
Used when PriceAssistAgent classifies intent as `knowledge` or `hybrid`.

Both tools are registered with PriceAssistAgent via the agent factory. The MAF agent runner calls them as part of its tool-use loop.

---

## Safeguards

### 1. Depth Guard
**Mechanism:** `_peer_depth` — a `ContextVar[int]` initialized to 0

Prevents infinite delegation loops (e.g. PriceAssist → DataAgent → PriceAssist → ...).

```python
if _peer_depth.get() >= 2:
    return "Error: maximum delegation depth exceeded"
```

Max depth: **2**. In practice the mesh never goes beyond depth 1 (PriceAssist → DataAgent/RAGAgent), so this is a safety net.

---

### 2. Deduplication Cache
**Mechanism:** `_peer_cache` — a `ContextVar[dict]` keyed by `(node_name, question)`

Within a single request, if PriceAssistAgent calls the same tool with the same question twice (possible in hybrid scenarios), the second call returns the cached result immediately.

```python
cache_key = (node_name, question)
if cache_key in _peer_cache.get():
    return _peer_cache.get()[cache_key]
```

---

### 3. Retry on Transient Failure
One retry on transient A2A transport failures (connection refused, timeout) with a **0.75 s delay**.

---

### 4. Echo Detection
If the peer agent returns the input question verbatim as its answer, the tool treats it as a rate-limit or LLM failure:

```python
if peer_response.strip() == question.strip():
    # treat as failure, return error message
```

This prevents an echo from being passed back to PriceAssistAgent as valid data.

---

### 5. Reasoning Extraction
`<llm_reasoning>` blocks inside peer responses are:
1. Stripped from the text returned to PriceAssistAgent (clean answer only)
2. Accumulated in `_peer_reasoning` — a `ContextVar[list]`
3. Written to a per-request temp file: `data/logs/.peer_{request_id}.json`

The API server reads this temp file after domain execution and merges the peer reasoning entries into the `MeshResult.llm_reasoning` array, so the frontend's AI Reasoning panel shows reasoning from DataAgent/RAGAgent as well.

---

## ContextVars — Request Isolation

All state is stored in Python `ContextVar`s, which are automatically scoped per async task. This means concurrent requests never share depth counters, caches, or reasoning buffers.

| ContextVar | Type | Purpose |
|---|---|---|
| `_peer_depth` | `int` | Current delegation depth |
| `_peer_cache` | `dict` | Dedup cache for this request |
| `_peer_reasoning` | `list` | Accumulated peer reasoning entries |

---

## OTel Metrics

From `src/observability/metrics.py`:
- `fab.a2a.calls.total` — incremented per A2A tool call, labelled by target agent
- `fab.a2a.duration` — histogram of A2A call latency in ms
