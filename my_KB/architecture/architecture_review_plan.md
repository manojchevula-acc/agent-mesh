# AgentMesh Architecture Review — Senior Agentic AI Platform Architect

## Context

This is a production-grade multi-agent banking AI platform (FAB — First Abu Dhabi Bank) built on Microsoft Agent Framework (MAF). It orchestrates four specialized agents (ComplianceAgent, PriceAssistAgent, DataAgent, RAGAgent) behind a defense-in-depth security pipeline, backed by a MySQL-based pricing data layer and a hybrid vector/BM25 RAG service. The review covers six architectural domains: MCP wiring, RAG pipeline, data layer delegation, agent instructions, tool calling patterns, and agent-to-agent orchestration.

---

## Phase 2 — Architecture Analysis

### Area 1: MCP Client ↔ Server Interaction

**How it's wired:**
- `src/integrations/mcp_clients.py` creates `MCPStreamableHTTPTool` instances pointing to:
  - DataLayer MCP: `http://127.0.0.1:9100/mcp` (FastMCP 2.0)
  - RAG MCP: `http://127.0.0.1:9000/mcp` (mcp[cli] 1.28)
- `a2a_server.py` wraps the tool in `async with tool:` with up to 8 exponential-backoff retries **at startup only**, keeping the connection alive for the node's lifetime.
- Tool schemas are auto-discovered on connect — no hand-written per-tool wrappers.

✅ **Working correctly:**
- Two different MCP frameworks (FastMCP, mcp[cli]) are both reachable via the same `MCPStreamableHTTPTool` client; protocol is spec-compliant.
- Auto-discovery means adding/removing tools from the server propagates automatically to the agent with no code change.
- Optional `header_provider` lambda for RAG API key is clean and non-invasive.
- Retry-with-backoff at startup correctly handles slow MCP server starts.

⚠️ **Questionable / Risky:**
- **Single-session lifetime**: The `async with tool:` is entered once at startup. If the DataLayer or RAG MCP server **restarts mid-flight** (crash, deploy, OOM), the tool's HTTP session is dead. All subsequent agent calls return connection errors until the A2A node itself is restarted. There is no reconnection watchdog.
- **No schema contract validation**: When the MCP server changes a tool's input schema, the consuming agent silently sends the old schema until it gets a runtime error. There is no CI check or startup-time schema diff.
- **Different framework versions in the same ecosystem**: FastMCP 2.0 (DataLayer) and mcp[cli] 1.28 (RAG). If a protocol breaking change lands in one before the other, the `/mcp` endpoint may return incompatible frames.

❌ **Broken / Architecturally Wrong:**
- Nothing is truly broken, but the lack of reconnect is a reliability hole that **will** manifest in production under rolling restarts or OOM kills.

🔧 **Recommendation:**

Wrap the `async with tool:` in a reconnect loop inside `a2a_server.py`. The simplest approach:

```python
# a2a_server.py — replace startup block with:
async def lifespan_with_reconnect(mcp_factory, agent, card, port):
    while True:
        try:
            tool = mcp_factory()
            async with tool:
                agent.register_tool(tool)  # re-register on each connect
                await serve_forever(agent, card, port)
        except Exception as exc:
            logger.warning("MCP connection lost (%s), reconnecting in 5s", exc)
            await asyncio.sleep(5)
```

For schema contract safety, add a startup assertion:
```python
discovered = {t.name for t in tool.tools}
expected = {"customer_360", "pricing_recommendation", ...}  # frozen set
assert expected <= discovered, f"MCP schema mismatch: missing {expected - discovered}"
```

---

### Area 2: RAG Agent ↔ RAG-as-a-Service

**How it's wired:**
- RAGAgent calls `search_documents(query, top_k, generate_answer=False)` via MCP.
- RAG service runs a 5-stage pipeline: encode (dense 1024-d + SPLADE sparse) → hybrid ANN+BM25 → cross-encoder rerank → freshness penalty → parent chunk expansion.
- Agent receives raw chunks (`source`, `clause`, `section`, `effective_date`, `stale`, `score`, `text`) and synthesizes citations itself.

✅ **Working correctly:**
- `generate_answer=False` is the right default for a multi-agent system — the coordinating LLM (PriceAssistAgent) should own synthesis, not the sub-service.
- Hybrid retrieval (dense + sparse + RRF) is state-of-the-art for regulatory document retrieval.
- Cross-encoder rerank with BAAI/bge-reranker-v2-m3 is appropriate for banking policy text.
- Parent chunk expansion (1500-token context) prevents answer truncation at clause boundaries.
- Freshness metadata (`stale`, `effective_date`) is returned — the data is there.

⚠️ **Questionable / Risky:**
- **Freshness handling absent in agent instructions**: The RAGAgent system prompt instructs citation format but says nothing about `stale=true` chunks or `freshness_warning=true` responses. A stale pricing floor could be cited to a customer without any caveat.
- **Empty result handling absent**: No instruction for what the agent should say when `total_results=0`. It may hallucinate or return a generic "I don't know" with no further action.
- **Context window pressure**: With `top_k=5` and parent expansion up to 1500 tokens per chunk, the agent prompt could inject 7,500+ tokens of chunk text into a model (`qwen/qwen3.6-27b`) that Groq serves with strict rate limits. Under heavy load, this can cause silent truncation.
- **Double synthesis risk**: If `RAG_GENERATE_ANSWER=true` is ever toggled on, the RAG MCP server generates an answer AND returns raw chunks. The RAGAgent will then synthesize again on top of the pre-generated answer. The instructions do not guard against this.

❌ **Broken / Architecturally Wrong:**
- **Staleness is collected but never acted on.** Returning stale policy data to a banker making pricing decisions is a compliance risk. The RAG pipeline correctly surfaces it; the agent layer discards it.

🔧 **Recommendation:**

Add explicit staleness handling to RAGAgent's system prompt:
```
If any retrieved chunk has stale=true or the response contains freshness_warning=true,
prefix your answer with: "⚠️ Note: Some source documents may be outdated (effective date
indicated). Verify against the latest policy before acting on this guidance."
```

Add empty-result fallback:
```
If total_results=0, respond: "No relevant policy documents were found for this query.
Please escalate to your compliance team for manual review."
```

For context pressure, reduce `top_k` default to 3 for the RAGAgent and rely on reranking quality rather than volume.

---

### Area 3: Data Agent ↔ DataLayer-as-a-Service

**How it's wired:**
- DataAgent connects to DataLayer MCP (`http://127.0.0.1:9100/mcp`) and auto-discovers 18 tools backed by `fab_semantic` MySQL views.
- All queries use parameterized SQL; never hit raw/curated tables directly.
- Max 15 rows returned when no filter is provided; errors returned as `{"error": "..."}` dicts.

✅ **Working correctly:**
- Semantic view isolation (never querying `fab_raw`/`fab_curated` directly) is the right pattern — the MCP server is a clean API contract over the data model.
- Parameterized SQL throughout eliminates SQL injection risk.
- Returning error dicts instead of raising exceptions keeps the agent in control of failure messaging.
- The price rebuild formula encoded in views (`treasury_rate + target_margin + risk_premium + ops_cost - relationship_discount`) is sound.

⚠️ **Questionable / Risky:**
- **Silent row truncation**: The 15-row cap applies when `customer_id` is empty. The DataAgent instructions say to extract `customer_id` from the request, but a bulk query ("show all non-compliant deals") will be silently truncated. The agent will not know it received a partial result.
- **No explicit error-handling instructions**: The DataAgent prompt says "Never invent data" but doesn't say what to do when a tool returns `{"error": "..."}`. The LLM may paraphrase the error, retry with different parameters, or in rare cases hallucinate an answer.
- **MySQL views, not materialized views**: The semantic views do multi-table joins (e.g., `pricing_recommendation_view` joins 5 tables). Under concurrent load with large datasets, these will degrade. There is no caching layer between the MCP server and MySQL.
- **Pool recycle at 1800s**: SQLAlchemy pool recycle is set to 30 minutes. MySQL's default `wait_timeout` is 8 hours — this is fine. But if MySQL is behind a proxy (AWS RDS Proxy, PgBouncer-style) with aggressive idle timeouts, 1800s may still be too long.

❌ **Broken / Architecturally Wrong:**
- **No row-level security at the data layer.** The RBAC check in the mesh workflow validates that the user's role is a known FAB role, but it does not prevent a `relationship_manager` from querying data for customers outside their portfolio. The DataAgent accepts any `customer_id` and returns data. A bad actor with a valid RM token can enumerate all customers.

🔧 **Recommendation:**

Pass `customer_id` context from `MeshState` (the authenticated user's context) into the DataAgent call via W3C baggage, and validate in the MCP server:

```python
# In mcp_server/tools.py, add a guard:
def _check_customer_access(requesting_role: str, requesting_user: str, target_customer_id: str):
    if requesting_role == "customer" and requesting_user != target_customer_id:
        raise PermissionError(f"Role 'customer' may only query their own data")
    # RM: validate against portfolio table (future)
```

For the truncation issue, add a `row_count` and `truncated` field to all tool responses:
```json
{"rows": [...], "row_count": 15, "truncated": true, "total_available": 47}
```

---

### Area 4: Agent Instructions

**ComplianceAgent:**
✅ Verdict format is unambiguous (`COMPLIANCE_PASSED:` / `COMPLIANCE_FAILED:` prefix).
✅ Scope is narrow: intent classification only, no tool access.
✅ Bypass logic is correctly handled in `ComplianceExecutor` before the agent is called — the agent itself doesn't need to know about bypasses.
⚠️ Uses `openai/gpt-oss-20b` — a smaller, faster model. For adversarial jailbreak detection, smaller models are more susceptible to sophisticated prompt injection patterns that a larger model (`gpt-oss-120b`) would catch. The tradeoff (latency vs. safety) is not documented.
⚠️ The instruction lists detection categories (injection, PII exfiltration, destructive, social engineering) but doesn't define severity levels. All violations produce the same `COMPLIANCE_FAILED` verdict — a mild scope question and a jailbreak attempt are treated identically. This blocks legitimate edge-case queries.

**DataAgent:**
✅ "Never invent data" constraint is explicit.
✅ `<llm_reasoning>` block emission for `tool_selection` + `data_synthesis` phases.
⚠️ No instruction on behavior when `customer_id` is absent or ambiguous.
⚠️ No instruction on what to do when a tool returns `{"error": "..."}`.
❌ No instruction to surface truncation (`truncated=true`) to the caller.

**RAGAgent:**
✅ Citation format `[Source: <doc_name>, Section <id>]` is specified.
⚠️ No staleness handling instruction (see Area 2).
⚠️ No empty-result fallback instruction.
⚠️ No instruction to prefer higher-score chunks when synthesizing — the agent may equally weight a 0.95-score chunk and a 0.52-score chunk.

**PriceAssistAgent:**
✅ Best-documented instruction set: intent classification matrix, delegation decision tree, response structure, word limit.
✅ Hybrid routing ("Is CUST001's loan compliant with policy?" → both tools) is explicitly modeled.
⚠️ "Always call appropriate tool(s) before answering" is stated, but there is no hard constraint preventing the LLM from answering from parametric memory when tools fail. In a banking context, an answer derived from training data (not live customer data) is a hallucination risk.
⚠️ No explicit instruction on what to do if **both** tool calls fail — the agent may compose a "best effort" answer from partial data.
❌ No role-awareness: the PriceAssistAgent doesn't know the requestor's role. A `customer` role user asking "What are the margins for all corporate clients?" should be deflected at this layer, but the agent has no visibility into role context.

🔧 **Recommendation for all agents:**

1. Add role context to every A2A call. The DomainExecutor already injects history; it should also inject a `[User context: role={role}]` line. Each agent instruction should include: "If the request is outside the scope permitted for this role, respond with: 'This information is not available for your access level.'"

2. For PriceAssistAgent, add a hard fallback constraint:
```
If all tool calls return error strings (DATA_UNAVAILABLE, RAG_UNAVAILABLE), 
respond ONLY with: "I was unable to retrieve the required data. Please try again 
or contact your relationship manager." Never answer from general knowledge.
```

3. For ComplianceAgent, add a severity tier to the verdict:
```
COMPLIANCE_PASSED: <reason>
COMPLIANCE_FLAGGED: <reason>   ← borderline, flag but allow
COMPLIANCE_FAILED: <reason>
```

---

### Area 5: Tool Calling Patterns

**Collaboration Tools (`query_structured_data`, `query_knowledge_base`):**
✅ Depth guard (`_peer_depth` ContextVar, max 2) correctly prevents infinite A2A recursion.
✅ Per-request deduplication cache (`_peer_cache`) prevents redundant Groq API calls.
✅ Soft-fail pattern: exceptions return error strings, not raised exceptions. The LLM stays in control.
✅ Echo detection correctly identifies Groq rate-limit responses ("I have called the tool").
✅ `<llm_reasoning>` block extraction from peer responses and storage in `_peer_reasoning` ContextVar for upstream tracing is elegant.

⚠️ **Exact-string dedup**: The cache key is `(node, question)` using exact string matching. PriceAssistAgent may rephrase the same logical query across tool calls (e.g., "CUST001 margin" vs "margin for customer CUST001"). No deduplication occurs, burning Groq rate limit budget.

⚠️ **Sequential fan-out**: When a hybrid query needs both tools, PriceAssistAgent calls them sequentially (tool-use loop). Most modern LLMs support parallel tool calls but the coordination depends on the LLM's behavior, not explicit parallelism in the framework. A 60-second total A2A timeout means two 25-second tool calls in sequence risk timeout.

⚠️ **Test seam mismatch**: `workflow.py` injects `ask_remote` as a seam for testing (`build_mesh_workflow(ask)`). This injection covers ComplianceExecutor and DomainExecutor (workflow-level A2A calls). But the collaboration tools import the **global** `ask_remote` from `src.a2a.clients` directly — bypassing the injected seam. Unit tests that mock the injected `ask_remote` do not cover the data_agent/rag_agent hops. This gives false confidence in test coverage.

❌ **No retry logic for MCP tool calls.** If the DataLayer MCP returns a transient 500 or connection reset, the tool call fails immediately. The DataAgent returns an error string, PriceAssistAgent reports "DATA_UNAVAILABLE", and the user gets a failure response. A single retry with 500ms backoff would eliminate most transient failures.

🔧 **Recommendation:**

For parallel fan-out, use `asyncio.gather` in the collaboration tools when both are needed:
```python
# In collaboration_tools.py
async def query_both(data_q: str, rag_q: str) -> tuple[str, str]:
    return await asyncio.gather(
        _consult_peer("data_agent", data_q, "DATA_UNAVAILABLE"),
        _consult_peer("rag_agent", rag_q, "RAG_UNAVAILABLE"),
    )
```
This requires the PriceAssistAgent prompt to use a single compound tool call rather than two sequential calls — feasible with parallel tool use in the MAF framework.

For MCP retry:
```python
# In mcp_clients.py, wrap execution:
async def _execute_with_retry(tool, name, args, retries=2, backoff=0.5):
    for attempt in range(retries + 1):
        try:
            return await tool.execute(name, args)
        except (httpx.ConnectError, httpx.RemoteProtocolError) as e:
            if attempt == retries:
                raise
            await asyncio.sleep(backoff * (2 ** attempt))
```

For the test seam, update `collaboration_tools.py` to accept an injectable `ask_remote`:
```python
_ask_remote_fn = ask_remote  # default to real implementation

def set_ask_remote(fn):  # called by test setup
    global _ask_remote_fn
    _ask_remote_fn = fn
```

---

### Area 6: Agent-to-Agent Orchestration

**Call graph:**
```
CLI/API → Orchestrator (in-process)
  → Compliance Agent (A2A HTTP :8015)
  → PriceAssist Agent (A2A HTTP :8018)
      → Data Agent (A2A HTTP :8016)
          → DataLayer MCP (HTTP :9100)
              → MySQL fab_semantic
      → RAG Agent (A2A HTTP :8017)
          → RAG MCP (HTTP :9000)
              → Qdrant + BGE-M3
```

✅ **Working correctly:**
- Strictly hierarchical dependency graph — no circular dependencies possible.
- W3C baggage propagation (`traceparent`/`tracestate`) provides end-to-end distributed tracing across all A2A hops.
- `TraceContextMiddleware` on each A2A node correctly continues the caller's trace.
- Soft-fail at every hop — a failing peer node degrades gracefully rather than cascading.
- The `launch_mesh.py` startup order (compliance → data → rag → price_assist) respects the dependency graph.

⚠️ **Questionable / Risky:**
- **No health-check gate in `launch_mesh.py`**: Nodes are started sequentially with `subprocess.Popen` but the launcher does not wait for each node to become healthy before starting the next. If `compliance` takes 8 seconds to load (Groq client init, OTel setup), `data_agent` starts immediately after with no guarantee the port is ready. First request arrives, ComplianceExecutor calls `:8015` — connection refused.
- **No circuit breaker on A2A calls**: If `price_assist` is slow (Groq latency spike), requests queue up behind the `A2A_TIMEOUT=60s` wall. With enough concurrent users, the orchestrator blocks all threads. There is no fallback response ("system temporarily unavailable") and no rejection of in-flight requests when overloaded.
- **Single orchestrator (PriceAssistAgent)**: All domain routing flows through one agent. If PriceAssist is the bottleneck (most complex model, longest prompt), there's no horizontal scaling path. Running multiple replicas on different ports is not modeled.
- **`devui_app.py` single-process mode**: The DevUI runs all agents in-process (not as separate A2A servers). The `build_devui_workflow` bypasses the A2A network. While this is great for development, it silently bypasses the `TraceContextMiddleware`, MCP reconnect logic, and actual network failure modes. DevUI test results cannot be trusted as representative of multi-process behavior.

❌ **Broken / Architecturally Wrong:**
- **`launch_mesh.py` races**: In a clean environment, starting a uvicorn server takes 1–2 seconds. The subprocess is forked but port binding takes another 100–500ms. If the next step in the chain tries to call the previous node before it's bound, the call fails. There is no `wait_for_port(host, port, timeout=30)` guard. This is a reliability bug that will manifest in CI and production cold starts.

🔧 **Recommendation:**

Add a port-readiness check in `launch_mesh.py`:
```python
import socket, time

def wait_for_port(host: str, port: int, timeout: float = 30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    raise TimeoutError(f"Port {port} not ready after {timeout}s")

# In launch_mesh.py, after each Popen:
wait_for_port(Config.A2A_HOST, port)
```

Add a basic circuit breaker on the orchestrator side using a token-bucket or sliding-window counter:
```python
# Simplest viable circuit breaker — open circuit on 3 consecutive failures
class CircuitBreaker:
    def __init__(self, threshold=3, reset_after=30):
        self._failures = 0
        self._open_until = 0
        self._threshold = threshold
        self._reset_after = reset_after
    
    def call(self, fn):
        if time.monotonic() < self._open_until:
            raise ServiceUnavailableError("Circuit open")
        try:
            result = fn()
            self._failures = 0
            return result
        except Exception:
            self._failures += 1
            if self._failures >= self._threshold:
                self._open_until = time.monotonic() + self._reset_after
            raise
```

---

## Phase 3 — Prioritized Action Plan

### 🔴 Critical (Fix Before Production)

1. **MCP reconnect on server restart** — `a2a_server.py`
   - Add reconnect loop around `async with tool:` with exponential backoff.
   - Without this, any DataLayer/RAG restart kills the agent node silently.

2. **Row-level security in DataAgent / MCP tools** — `mcp_server/tools.py`, `workflow.py`
   - Pass authenticated user + role via W3C baggage into MCP tool calls.
   - Add customer ownership check: `customer` role → only own data; `RM` → only portfolio.
   - Current state allows any authenticated user to enumerate all customer records.

3. **Session ID ownership validation** — `api_server.py`, `orchestrator.py`
   - `GET /api/conversations/{session_id}` has no ownership check.
   - Any logged-in user can read any other user's conversation history by guessing a UUID.
   - Add `user_name` binding to session_id in the JSONL store and validate on load.

4. **`launch_mesh.py` port-readiness gate** — `launch_mesh.py`
   - Add `wait_for_port()` after each `Popen` call.
   - Prevents cold-start race conditions in CI and production restarts.

### 🟠 High (Address in Next Sprint)

5. **Staleness handling in RAGAgent instruction** — `src/agents/rag_agent.py`
   - Add explicit instruction to prefix stale citations with a policy caveat.
   - Banking compliance: citing an outdated pricing floor is a regulatory risk.

6. **Hard fallback in PriceAssistAgent instruction** — `src/agents/price_assist_agent.py`
   - "If all tool calls fail, return a fixed cannot-answer message. Never answer from training data."
   - Without this, the LLM may hallucinate pricing data, which is dangerous in a banking context.

7. **MCP tool call retry logic** — `src/integrations/mcp_clients.py`
   - 2 retries with 500ms/1s backoff on `ConnectError` / `RemoteProtocolError`.
   - Eliminates most transient network failures.

8. **Role context injection into agent calls** — `src/mesh/workflow.py` DomainExecutor
   - Add `[User role: {role}]` to every A2A call payload.
   - Update DataAgent and RAGAgent instructions to use role for scope enforcement.

9. **`launch_mesh.py` circuit breaker / health API** — `a2a_server.py`, `launch_mesh.py`
   - Add `/health` endpoint to each A2A node (it exists in `api_server.py` but not on agent nodes).
   - Add basic circuit-breaker on `ask_remote` calls in the orchestrator.

### 🟡 Medium (Improvements)

10. **Parallel fan-out for hybrid queries** — `src/tools/collaboration_tools.py`
    - Use `asyncio.gather` when PriceAssistAgent needs both tools.
    - Reduces hybrid query latency by ~50% under nominal Groq response times.

11. **Fix test seam for collaboration tools** — `src/tools/collaboration_tools.py`
    - Make `ask_remote` injectable for unit tests.
    - Current mock in tests doesn't cover the data_agent/rag_agent hops.

12. **Add `truncated` flag to MCP tool responses** — `datalayer-as-service/mcp_server/tools.py`
    - Return `{"rows": [...], "row_count": N, "truncated": true}` when 15-row cap applies.
    - DataAgent instructions should surface this to the caller.

13. **Upgrade ComplianceAgent to severity tiers** — `src/agents/compliance_agent.py`
    - Add `COMPLIANCE_FLAGGED` verdict for borderline cases.
    - Prevents over-blocking of legitimate edge-case banking queries.

14. **MCP startup schema assertion** — `src/integrations/mcp_clients.py`
    - Assert expected tool names on connect.
    - Catch server-side schema drift at startup, not at runtime.

15. **Score-weighted synthesis in RAGAgent** — `src/agents/rag_agent.py`
    - Instruct agent to weight citations by retrieval score; deprioritize chunks below 0.7.
    - Improves answer quality for multi-chunk responses.

### 🟢 Nice-to-Have (Technical Debt)

16. **Redis backend implementation** — `src/memory/redis_backend.py`
    - Currently a stub. Required for multi-instance horizontal scaling.
    - Without it, JSONL sessions are node-local — multi-process setups share no memory.

17. **DevUI parity with multi-process mode** — `devui_app.py`
    - DevUI currently bypasses A2A networking entirely.
    - Consider running a lightweight in-process HTTP server per agent so DevUI exercises the real code paths.

18. **Compliance model upgrade consideration** — `src/config.py`
    - `COMPLIANCE_MODEL=openai/gpt-oss-20b` is the smallest model in the mesh.
    - Document the latency/safety tradeoff; benchmark adversarial inputs against a larger model.

19. **Semantic deduplication in peer cache** — `src/tools/collaboration_tools.py`
    - Replace exact-string dedup with an embedding similarity check (cosine > 0.95 = cache hit).
    - Low priority — only matters under high Groq rate-limit pressure.

20. **API versioning** — `api_server.py`
    - Add `/api/v1/` prefix to all endpoints.
    - Enables non-breaking evolution of the REST contract.

---

## Verification Approach

After implementing any fix:

1. **Unit**: Run `test_agent_mesh.py` — exercises guardrails, RBAC, compliance bypass, conversation memory, and PII redaction with mocked A2A.
2. **Integration**: Start full mesh with `launch_mesh.py`, submit test queries via `run.py --verbose` — verify trace spans appear in the CLI renderer and `data/trace_log.jsonl`.
3. **Security**: Submit adversarial test cases (injection patterns, cross-customer customer_ids, stale session_ids) against the running REST API (`api_server.py`).
4. **Resilience**: Restart DataLayer MCP server while agent mesh is running — verify reconnect logic recovers without restarting A2A nodes.
5. **RAG freshness**: Ingest a document with an old `effective_date`, query via RAGAgent — verify staleness warning appears in the response.
