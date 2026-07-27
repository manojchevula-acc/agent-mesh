# The Four AI Agents

AgentMesh runs four specialized agents as isolated OS processes, each hosting an A2A HTTP server. They communicate via the **Google A2A protocol** (JSON-RPC/HTTP) on the **Microsoft Agent Framework (MAF)**.

---

## Agent Map

```
                        ┌─────────────────────────┐
                        │   PriceAssistAgent :8018 │  ← Primary orchestrator
                        │  (FAB banking assistant) │
                        └────────────┬────────────┘
                    ┌────────────────┴─────────────────┐
                    ▼                                   ▼
        ┌──────────────────────┐           ┌──────────────────────┐
        │   DataAgent :8016    │           │   RAGAgent :8017     │
        │  (structured data)   │           │  (policy knowledge)  │
        └──────────┬───────────┘           └──────────┬───────────┘
                   ▼                                   ▼
        DataLayer MCP :9100                  RAG MCP :9000
        (18 SQL-view tools)              (search_documents tool)

        ┌──────────────────────────────────────────────────────┐
        │              ComplianceAgent :8015                   │
        │   (screens every request before domain execution)    │
        └──────────────────────────────────────────────────────┘
```

---

## 1. ComplianceAgent

**File:** `src/agents/compliance_agent.py`  
**Port:** 8015  
**Model:** `COMPLIANCE_MODEL` (default `openai/gpt-oss-20b`)

**Role:** Semantic safety guardrail. Every request passes through this agent before domain execution.

**What it checks (7 categories):**
1. Prompt injection / jailbreak attempt
2. PII exfiltration attempt
3. Destructive command intent
4. Social engineering
5. Context poisoning
6. Scope violation (query outside user's RBAC scope)
7. Authorization: is this role permitted to perform this task?

**Output format:**
```
COMPLIANCE_PASSED
<llm_reasoning>{"phase": "safety_review", "checks": [...], "authorization": "..."}</llm_reasoning>
```
or
```
COMPLIANCE_FAILED: <reason>
<llm_reasoning>...</llm_reasoning>
```

**Bypasses:**
- `platform_administrator` and `operations_manager` roles skip the A2A call entirely — the executor stamps `COMPLIANCE_PASSED` directly.

**No tools** — pure LLM reasoning only.

---

## 2. DataAgent

**File:** `src/agents/data_agent.py`  
**Port:** 8016  
**Model:** `DATA_AGENT_MODEL` (default `qwen/qwen3.6-27b`)

**Role:** Thin MCP client for structured banking data. Holds zero business logic — all logic lives in the external DataLayer-as-a-Service.

**How it works:**
- On startup, `MCPStreamableHTTPTool` connects to `DATALAYER_MCP_URL` (default `http://127.0.0.1:9100/mcp`)
- Auto-discovers **18 SQL-view tools** exposed by the DataLayer service (customer data, deal data, pricing, portfolios, etc.)
- The MAF agent receives a query, selects the right tool(s), calls them, and synthesizes an answer
- `max_function_calls=4` — prevents runaway recursive tool calls

**Called by:** PriceAssistAgent via `query_structured_data` collaboration tool → A2A

**Embeds:** `<llm_reasoning>{"phase": "tool_selection", ...}</llm_reasoning>` per tool call for transparency

---

## 3. RAGAgent

**File:** `src/agents/rag_agent.py`  
**Port:** 8017  
**Model:** `RAG_AGENT_MODEL` (default `qwen/qwen3.6-27b`)

**Role:** Thin MCP client for unstructured knowledge (policy documents, regulatory rules). Like DataAgent, holds no business logic.

**How it works:**
- `MCPStreamableHTTPTool` connects to `RAG_MCP_URL` (default `http://127.0.0.1:9000/mcp`)
- Single tool: `search_documents` (vector/hybrid retrieval over the policy KB)
- `max_function_calls=1` — exactly one search per request by design

**Called by:** PriceAssistAgent via `query_knowledge_base` collaboration tool → A2A

**Embeds:** `<llm_reasoning>{"phase": "tool_selection", ...}</llm_reasoning>` per search call

---

## 4. PriceAssistAgent

**File:** `src/agents/price_assist_agent.py`  
**Port:** 8018  
**Model:** `PRICE_ASSIST_MODEL` (default `openai/gpt-oss-120b`)

**Role:** Primary FAB banking orchestrator. Classifies intent and delegates to specialist agents.

**Intent classification:**
| Intent | Action |
|---|---|
| `data` | Calls `query_structured_data` → DataAgent |
| `knowledge` | Calls `query_knowledge_base` → RAGAgent |
| `hybrid` | Calls both tools, synthesizes answer |

**Tools available:**
- `query_structured_data(question: str)` → delegates to DataAgent
- `query_knowledge_base(question: str)` → delegates to RAGAgent

**Conversation context injection:**  
If rolling summarization is active, DomainExecutor prepends a `[Conversation Summary]` block to the prompt before calling this agent.

**Role-based scope enforcement:**  
The RBAC scope (allowed tasks, denied tasks) is injected into the system prompt so the agent refuses out-of-scope requests even if they pass compliance.

**Reasoning output:**
- `<llm_reasoning>{"phase": "intent_routing", ...}</llm_reasoning>` at intent classification
- `<llm_reasoning>{"phase": "synthesis", ...}</llm_reasoning>` at answer synthesis

---

## Agent Factory & Middleware

**File:** `src/agents/agent_factory.py:create_demo_agent()`

Every agent is wired with:
- `AuditMiddleware` — appends a JSONL record to `data/audit_trail.jsonl` per invocation
- `ToolCallLogMiddleware` — logs each MCP/A2A tool call with latency
- The Groq OpenAI-compat MAF client (model + API key per agent)
- Optional tool list (MCP tools for Data/RAG agents; A2A collaboration tools for PriceAssist)

**File:** `src/agents/node_registry.py`

`AGENT_REGISTRY` maps node names (`compliance`, `data_agent`, `rag_agent`, `price_assist`) to builder functions.  
`MCP_BACKED_NODES = {"data_agent", "rag_agent"}` — signals `a2a_server.py` to hold an MCP session open for the node's lifetime.

---

## A2A Protocol Details

**Files:** `src/a2a/hosting.py`, `src/a2a/clients.py`

- Each agent node is wrapped in a Starlette A2A server via `hosting.py`
- Cross-process calls use `ask_remote(agent_url, message)` from `clients.py`
- W3C `traceparent` + `tracestate` headers propagate automatically via OTel httpx instrumentation so all spans join one end-to-end distributed trace
- W3C baggage (`fab.request_id`, `fab.user`, `fab.role`, `fab.session_id`) flows through every hop
