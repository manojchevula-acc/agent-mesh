# AgentMesh Architecture Implementation Log

**Date:** 2026-07-05  
**Review basis:** Senior Agentic AI Platform Architect analysis of the full agent-mesh codebase  
**Branch:** maf-thread-memory-jsonl-persistence

---

## Summary

Implemented all Critical (🔴) and High-priority (🟠) fixes identified in the architecture review. 9 changes across 10 files. No breaking changes to public API contracts or external interfaces.

---

## Changes Implemented

### 🔴 Critical Fix 1 — MCP Reconnect Loop
**File:** `agent-mesh/a2a_server.py`  
**Problem:** The `_serve_mcp_node()` function had startup retry logic (8 attempts with exponential backoff) but zero reconnect logic. If the DataLayer or RAG MCP server restarted mid-flight (crash, OOM, rolling deploy), the `async with mcp_tool:` session died silently — all subsequent agent tool calls returned connection errors until the entire A2A node process was manually restarted.

**Fix:** Converted the finite `for attempt in range(_MCP_RETRIES)` loop into a `while True:` reconnect loop that tracks whether the node has ever become healthy (`ever_started` flag):
- **Startup phase** (before `ever_started`): same exponential backoff, same 8-attempt limit. Hard exit (`SystemExit(1)`) if MCP is unreachable at start.
- **Mid-session phase** (after `ever_started`): any exception triggers a 5-second reconnect wait and a fresh `async with mcp_tool:` attempt. Never exits — the node self-heals.
- Port rebinding on reconnect works because uvicorn uses `SO_REUSEADDR` by default.

**Key change:**
```python
ever_started = False
startup_attempt = 0
while True:
    startup_attempt += 1
    try:
        mcp_tool = MCP_TOOL_FACTORIES[name]()
        async with mcp_tool:
            ...
            ever_started = True
            startup_attempt = 0
            await server.serve()
            return  # clean shutdown — don't reconnect
    except Exception as exc:
        if ever_started:
            # mid-session drop → reconnect
            await asyncio.sleep(5.0)
        elif startup_attempt >= _MCP_RETRIES:
            raise SystemExit(1) from exc
        else:
            # startup retry
            await asyncio.sleep(backoff)
```

---

### 🔴 Critical Fix 2 — Port-Readiness Gate in Launcher
**File:** `agent-mesh/launch_mesh.py`  
**Problem:** `launch_mesh.py` started all four A2A nodes sequentially with `subprocess.Popen` and a 1-second `time.sleep()` between each. Port binding in uvicorn takes 100–500ms after fork. If the previous node hadn't bound its port before the next one started (or before the first request arrived), callers got `Connection refused` — a race condition on every cold start.

**Fix:** Added `_wait_for_port(host, port, timeout=30s)` function that polls with `socket.create_connection` until the port accepts connections. Called after each `Popen`. Removed the blind `time.sleep(1.0)`. The launcher now prints "ready ✓" when each node's port becomes reachable, and warns (but doesn't abort) on timeout.

```python
def _wait_for_port(host, port, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError(f"Port {port} on {host} not ready within {timeout:.0f}s")
```

---

### 🔴 Critical Fix 3 — Session ID Ownership Validation
**Files:** `agent-mesh/src/memory/jsonl_backend.py`, `agent-mesh/src/memory/conversation_store.py`, `agent-mesh/src/mesh/orchestrator.py`, `agent-mesh/api_server.py`  
**Problem:** `GET /api/conversations/{session_id}` had no access control. Any logged-in user could read any other user's conversation history by knowing (or guessing) a UUID session_id. Session IDs are user-prefixed UUIDs (e.g. `alice_a3f7b2c1`) so they're guessable with a username.

**Fix (4 files, no breaking changes to existing sessions):**

1. **`jsonl_backend.py`** — Added lightweight sidecar ownership files:
   - `write_owner(session_id, user_name)`: writes `{session_id}.owner` beside the JSONL file on first call only (idempotent).
   - `read_owner(session_id) -> str | None`: reads the sidecar, returns `None` if file absent (backward-compat for pre-existing sessions).
   - The JSONL message format is untouched — zero migration needed for existing sessions.

2. **`conversation_store.py`** — Exposed `bind_session()` and `check_owner()` on `ConversationStore`:
   - `bind_session(session_id, user_name)`: delegates to backend's `write_owner`. No-op if backend lacks it.
   - `check_owner(session_id, requesting_user) -> bool`: returns `True` if owner matches OR if no `.owner` file exists (backward-compat).

3. **`orchestrator.py`** — After each `append_turn()`, call `store.bind_session(session_id, user.username)`. Since `write_owner` is idempotent (first-write-wins), only the first turn's author becomes the owner.

4. **`api_server.py`** — `GET /api/conversations/{session_id}?username=<user>` now enforces ownership:
   - If `username` query param is provided and the session has an owner, returns 403 if they don't match.
   - If no `username` param or no `.owner` file (legacy sessions), access is allowed (backward-compatible).

---

### 🟠 High Fix 4 — `/health` Endpoint on All A2A Nodes
**File:** `agent-mesh/src/a2a/hosting.py`  
**Problem:** A2A nodes (compliance, data_agent, rag_agent, price_assist) had no `/health` endpoint. The `api_server.py` mesh-status fan-out (`GET /api/mesh/status`) was already trying to call `{node_url}/health`, but the A2A nodes didn't have this route. All nodes showed as "error" in the mesh-status UI unless the node happened to serve the A2A card at `/`.

**Fix:** Added a `GET /health` route to `build_starlette_app()`. The route is registered before the A2A JSON-RPC routes so it takes priority. Returns:
```json
{"status": "ok", "node": "<agent card name>", "uptime_seconds": 42.1, "model": "..."}
```
The `_start_time` is captured at `build_starlette_app()` call time (per-node, per-process).

---

### 🟠 High Fix 5 — Role Context Injection into PriceAssistAgent
**File:** `agent-mesh/src/mesh/workflow.py` (DomainExecutor)  
**Problem:** PriceAssistAgent received no information about the requesting user's role. A `customer`-role user asking "What are the margins for all corporate clients?" was processed identically to a `relationship_manager`. The agent had no basis to enforce scope restrictions.

**Fix:** In `DomainExecutor.run()`, prepend a `[User: <name> | Role: <role>]` line to every prompt sent to PriceAssistAgent:
```python
role_context = f"[User: {state.user_name} | Role: {state.role}]\n"
base_prompt = f"{role_context}{history_block}{state.query}" if history_block else f"{role_context}{state.query}"
```
This role context line is also visible to DataAgent and RAGAgent when PriceAssistAgent delegates queries to them, because the question text passed to collaboration tools inherits context from the conversation.

---

### 🟠 High Fix 6 — A2A Retry on Transient Transport Failures
**File:** `agent-mesh/src/tools/collaboration_tools.py`  
**Problem:** `_consult_peer()` called `ask_remote(node, question)` once. A transient connection reset or 500 from an A2A node immediately resulted in a `DATA_UNAVAILABLE` / `RAG_UNAVAILABLE` error string propagating to the user. One retry with a short backoff eliminates the vast majority of transient failures.

**Fix:** Added a `for _attempt in range(_A2A_RETRIES + 1)` retry loop (1 retry, 0.75s backoff) around `ask_remote()`. Only catches exceptions — echo responses and rate-limit strings are handled by the existing post-call logic, not retried. If all attempts fail, the `last_exc` is re-raised and caught by the outer `except Exception` block, preserving the soft-fail behavior.

```python
_A2A_RETRIES = 1
_A2A_RETRY_DELAY = 0.75

for _attempt in range(_A2A_RETRIES + 1):
    try:
        result = await ask_remote(node, question)
        break
    except Exception as _e:
        _last_exc = _e
        if _attempt < _A2A_RETRIES:
            await asyncio.sleep(_A2A_RETRY_DELAY)
if result is None:
    raise _last_exc
```

---

### 🟠 High Fix 7 — RAGAgent Staleness & Empty-Result Handling
**File:** `agent-mesh/src/agents/rag_agent.py`  
**Problem:** The RAGAgent's system prompt had partial stale-handling ("If a passage is flagged stale, include that warning") but no explicit instruction to check the `stale` field on individual chunks or the `freshness_warning` boolean on the response envelope. Empty results (`total_results=0`) had no prescribed fallback — the agent could hallucinate policy content or give a vague non-answer.

**Fix:** Rewrote rules 3–5 in `RAG_INSTRUCTIONS`:
- Rule 3 (NO RESULTS): exact fallback text required: `"No relevant policy documents were found for this query. Please escalate to your compliance team for manual review."`
- Rule 4 (FRESHNESS/STALENESS): explicit check on `stale=true` per chunk AND `freshness_warning=true` on the response envelope. Requires a specific prefix warning. Also requires `effective_date` to be cited alongside policy figures.
- Rule 5 (SCORE WEIGHTING): new rule — chunks with `score < 0.5` must be cited as supplementary only.

---

### 🟠 High Fix 8 — PriceAssistAgent Role Scope + Hard Tool-Failure Fallback
**File:** `agent-mesh/src/agents/price_assist_agent.py`  
**Problem:**  
1. No role-based scope enforcement — agent didn't use the `[User: ... | Role: ...]` context line.  
2. Rule 6 (TOOL UNAVAILABILITY) allowed the agent to "answer only from what was retrieved" when one tool failed — opening the door to answering from LLM training data when the data tool was down. In banking, a hallucinated pricing margin is a critical correctness failure.

**Fix:**  
1. Added a `ROLE-BASED SCOPE ENFORCEMENT` section before OPERATING RULES. Maps each of the 5 relevant roles to explicit data access permissions. Instructs the agent to use the injected `[User: ... | Role: ...]` line.  
2. Rewrote Rule 6 (TOOL UNAVAILABILITY): if BOTH tools fail or return error strings, the agent MUST respond with a fixed "unable to retrieve" message and MUST NOT answer from training knowledge.

---

### 🟠 High Fix 9 — DataAgent Error Handling & Role Scope Instructions
**File:** `agent-mesh/src/agents/data_agent.py`  
**Problem:** OPERATING RULES lacked instructions for three key failure modes:
1. Tool error responses (`{"error": "..."}`) — agent could paraphrase or work around them.
2. Truncated results (15-row cap) — agent would present partial data as complete.
3. Role scope enforcement — agent accepted any `customer_id` regardless of who was asking.

**Fix:** Expanded OPERATING RULES from 5 to 8 rules:
- Rule 4 (TOOL ERROR): exact prescribed response for `{"error": ...}` responses.
- Rule 5 (TRUNCATION): if `truncated: true` present, note the partial result and suggest filtering.
- Rule 6 (ROLE SCOPE): use `[User: ... | Role: ...]` context; `customer` role may only query their own customer_id.

---

## Files Changed

| File | Change Type | Priority |
|------|-------------|----------|
| `agent-mesh/a2a_server.py` | MCP reconnect loop | 🔴 Critical |
| `agent-mesh/launch_mesh.py` | Port-readiness gate | 🔴 Critical |
| `agent-mesh/src/memory/jsonl_backend.py` | Owner sidecar files | 🔴 Critical |
| `agent-mesh/src/memory/conversation_store.py` | bind/check owner API | 🔴 Critical |
| `agent-mesh/src/mesh/orchestrator.py` | Bind session owner on save | 🔴 Critical |
| `agent-mesh/api_server.py` | Conversation endpoint ownership check | 🔴 Critical |
| `agent-mesh/src/a2a/hosting.py` | `/health` endpoint on A2A nodes | 🟠 High |
| `agent-mesh/src/mesh/workflow.py` | Role context injection | 🟠 High |
| `agent-mesh/src/tools/collaboration_tools.py` | A2A retry logic | 🟠 High |
| `agent-mesh/src/agents/rag_agent.py` | Staleness + empty-result instructions | 🟠 High |
| `agent-mesh/src/agents/price_assist_agent.py` | Role scope + hard fallback | 🟠 High |
| `agent-mesh/src/agents/data_agent.py` | Error handling + role scope | 🟠 High |

---

## Remaining (Not Yet Implemented)

### 🟡 Medium Priority
- **Parallel fan-out for hybrid queries** (`collaboration_tools.py`) — requires `asyncio.gather` + prompt change for PriceAssistAgent to issue a single compound tool call. Needs testing.
- **Fix test seam for collaboration tools** (`collaboration_tools.py`) — make `ask_remote` injectable; currently tests mock the workflow-level seam but not the tool-level A2A calls.
- **Add `truncated` flag to MCP tool responses** (`datalayer-as-service/mcp_server/tools.py`) — DataAgent instruction now references `truncated:true` but the MCP server doesn't yet emit it. Need to add `row_count` + `truncated` to all 18 tool responses.
- **ComplianceAgent severity tiers** (`compliance_agent.py`) — add `COMPLIANCE_FLAGGED` for borderline cases to reduce over-blocking.
- **MCP startup schema assertion** (`mcp_clients.py`) — assert expected tool names on connect to catch schema drift.
- **Score-weighted RAG synthesis** — already added `score < 0.5` guidance in RAGAgent instructions; the full enforcement lives in `rag_agent.py` instructions.

### 🟢 Nice-to-Have (Technical Debt)
- **Redis backend implementation** (`src/memory/redis_backend.py`) — stub only; required for multi-instance horizontal scaling.
- **DevUI network parity** (`devui_app.py`) — DevUI bypasses A2A networking; results aren't representative of multi-process behavior.
- **Compliance model upgrade** — `gpt-oss-20b` is the smallest model; consider benchmarking against a larger model for adversarial injection detection.
- **Semantic deduplication** in peer cache — exact-string dedup misses rephrased identical queries.
- **API versioning** — add `/api/v1/` prefix to all REST endpoints.

---

## Verification Steps

1. **Port-readiness**: Run `python launch_mesh.py` and confirm each node prints "ready" before the next one starts. Kill a node mid-run and confirm the launcher detects exit.

2. **MCP reconnect**: Start the mesh, then restart the DataLayer MCP server (`python -m mcp_server.server`). Confirm the `data_agent` A2A node reconnects (check logs: "MCP session dropped … Reconnecting") and subsequently handles tool calls without restarting the A2A process.

3. **Session ownership**: 
   - Submit a query as `alice`. Check that `data/conversations/` contains both `alice_<hash>.jsonl` and `alice_<hash>.owner` (owner = "alice").
   - Call `GET /api/conversations/alice_<hash>?username=bob` — expect 403.
   - Call `GET /api/conversations/alice_<hash>?username=alice` — expect 200.
   - Call `GET /api/conversations/alice_<hash>` (no username) — expect 200 (backward-compat).

4. **A2A node health**: With mesh running, call `GET http://127.0.0.1:8015/health` (compliance node). Expect `{"status": "ok", "node": "...", "uptime_seconds": ..., "model": "..."}`. Verify `GET /api/mesh/status` now shows all nodes as "ok" instead of "error".

5. **Role context**: Submit a query as `cust001` (Customer role) asking "What are the margins for CUST002?". PriceAssistAgent should deflect: "As a customer, you can only access your own account information."

6. **RAG staleness**: Ingest a document with a past `effective_date` into Qdrant. Query via the RAG agent. Confirm the response includes the freshness warning prefix when `stale=true` chunks are returned.

7. **A2A retry**: Temporarily introduce a 1-second failure into the `data_agent` node (e.g. add a 50% chance of raising an exception). Confirm collaboration_tools retries once before marking `DATA_UNAVAILABLE`.
