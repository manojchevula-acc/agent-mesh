# FAB MCP Hub Simple — Technical Design

**Version:** 3.0 (as of 2026-07-21)

---

## 1. Overview

The FAB MCP Hub Simple is a lightweight, direct-connect agent system that routes natural-language queries to specialised MCP (Model Context Protocol) servers and streams the results back to a browser-based chat UI.

**What this system IS:**
- A MySQL-backed MCP server registry (`fab_semantic.mcp_servers`) managed by `hub_service/hub_server.py`
- A local agentic loop using Ollama llama3.2:3b (no API key, no cloud dependency)
- A FastAPI/SSE chat UI server with real-time event streaming for full traceability
- An agent (`agent.py`) that calls `POST /discover`, connects to the selected MCP server, discovers its tools live, and runs a native ReAct loop
- Optional Bearer token auth on both the hub API and all five MCP servers

**What this system is NOT:**
- It has no role-based policy or tool allowlists
- It has no audit trail or compliance logging
- It has no circuit breakers or rate limiting
- It has no PII scanning or guardrails

These capabilities belong to the Full Hub (existing `hub/` directory). The Simple Hub is for prototyping, demos, and exploration.

---

## 2. Architecture Diagram

Eight concurrent processes make up the running system:

```
  Browser
  ───────
     │  HTTP GET /
     │  POST /chat/stream (SSE)
     ▼
┌──────────────────────────────────────────────────────┐
│  chat_service/chat_server.py   FastAPI  :8080        │
│                                                      │
│  GET /           → serves SPA (HTML/CSS/JS inline)  │
│  POST /chat/stream → SSE: creates asyncio.Queue,     │
│                     starts run_agent as Task,        │
│                     streams events to browser        │
│  GET /health     → returns model + hub server URL    │
└──────────────────────┬───────────────────────────────┘
                       │  calls run_agent(query, on_event)
                       ▼
┌──────────────────────────────────────────────────────┐
│  agent.py   Orchestrator                             │
│                                                      │
│  run_agent()     — hub discovery → mcp_session →     │
│                    load_mcp_tools → ReAct agent       │
│  mcp_session()   — transport-aware MCP context mgr   │
│  _auth_headers() — Bearer token helper               │
│  _fmt()          — compact log formatter             │
└────────┬─────────────────────┬────────────────────────┘
         │  POST /discover     │  MCP protocol
         ▼                     ▼
┌──────────────────────────────────────────────────────┐
│  hub_service/hub_server.py   FastAPI REST  :8090     │
│                                                      │
│  GET  /health        → status (public)               │
│  GET  /servers       → list registry (auth required) │
│  GET  /servers/{id}  → single config  (auth required)│
│  POST /discover      → LLM routing → server config   │
│                         (auth required)              │
│  route_to_server()  — LLM agent or first-match       │
│  load_hub()         — MySQL query + 60s cache        │
└────────────────────────┬─────────────────────────────┘
                         │  queries
                         ▼
┌────────────────────────────────────────────────────┐
│  MySQL fab_semantic.mcp_servers                    │
│  5 rows — id, name, endpoint, transport,           │
│           description, examples, start_cmd         │
│  Seeded by: python scripts/seed_hub_db.py          │
│  Source:    hub_service/mcp-hub.json               │
└────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────┐
│              MCP Servers (5 total)                     │
│                                                        │
│  Demo servers (FastMCP streamable-HTTP):               │
│  ┌─────────────────────────────────────────────────┐  │
│  │  weather_server.py  :8001  /mcp/                │  │
│  │  calc_server.py     :8002  /mcp/                │  │
│  │  data_server.py     :8003  /mcp/                │  │
│  └─────────────────────────────────────────────────┘  │
│                                                        │
│  FAB Data Layer (FastMCP streamable-HTTP):             │
│  ┌─────────────────────────────────────────────────┐  │
│  │  customer_server.py :9100  /mcp/  (9 tools)     │  │
│  │  ─── connects to MySQL fab_semantic ───         │  │
│  └─────────────────────────────────────────────────┘  │
│  ┌─────────────────────────────────────────────────┐  │
│  │  pricing_server.py  :9200  /mcp/  (9 tools)     │  │
│  │  ─── connects to MySQL fab_semantic ───         │  │
│  └─────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────┘
                                        │
                                MySQL 8.4 :3306
                                fab_semantic schema
                                (16 views over 14 tables)
```

**Local LLM:** Ollama llama3.2:3b at `http://localhost:11434/v1` (OpenAI-compatible API)
Used for: hub routing decisions (ReAct agent), agentic tool selection in agent.py

---

## 3. Data Flow

### 3.1 Single-Server Query

Example: "What is the weather in Dubai?"

```
Step 1 — Browser sends POST /chat/stream with {"query": "What is the weather in Dubai?"}

Step 2 — chat_server.py creates asyncio.Queue, launches run_agent(query, on_event) as Task

Step 3 — run_agent() calls POST /discover to hub_server.py (port 8090)
  → hub_server.py queries MySQL fab_semantic.mcp_servers (60s in-process cache)
  → LLM routing agent injects server list into message, calls pick_server() with best match
  → returns list of server configs + routing metadata
  → run_agent() emits routing event

Step 4 — agent receives servers list [{id, endpoint, transport, ...}]
  - hub_server.py routing agent:
    - server context injected into message (capability, skills, description, examples)
    - called pick_server("weather-server", "weather query matches weather server capability")
  - emits routing event: {method: "agent", server_id: "weather-server", server_ids: ["weather-server"]}

Step 5 — mcp_session(weather_server) opens sse_client("http://localhost:8001/sse")
  - emits mcp_connecting event
  - ClientSession.initialize() performs MCP handshake
  - load_mcp_tools(session) discovers tools, emits mcp_connected event

Step 6 — create_react_agent built; astream_events loop begins:
  on_tool_start:
    - Ollama decides to call get_weather(city="Dubai")
    - emits tool_call event
  on_tool_end:
    - result returned from MCP server
    - emits tool_result event
  on_chat_model_end (no tool_calls):
    - Ollama produces final text → answer captured
    - returns "The current weather in Dubai is 38°C, sunny, humidity 45%..."

Step 7 — run_agent() emits final_answer event with content

Step 8 — chat_server.py event_generator reads from Queue, yields SSE lines to browser
  None sentinel signals end of stream → browser closes EventSource
```

### 3.2 FAB Customer Query

Example: "Get the 360 profile for CUST007"

```
Step 1 — Browser sends POST /chat/stream

Step 2 — run_agent() calls POST /discover to hub_server.py → emits hub_loaded event

Step 3 — hub routing agent runs:
  - server context injected: all 5 server configs (capability, skills, description, examples)
  - called pick_server("fab-customer-server", "CUST007 360 profile → customer intelligence domain")
  - Returns: {servers: [{fab-customer-server}], method: "agent"}
  - emits routing event: {method: "agent", server_id: "fab-customer-server", server_ids: ["fab-customer-server"]}

Step 4 — mcp_session(fab_customer_server) opened via streamablehttp_client

Step 5 — load_mcp_tools(session) → discovers 9 tools live from the server

Step 6 — create_react_agent built with those 9 tools; astream_events loop begins:
  - on_tool_start: customer_360(customer_id="CUST007")
  - on_tool_end: returns customer profile JSON
  - on_chat_model_end: final answer text (no tool_calls → captured)

Step 7 — run_agent() emits final_answer event

Step 8 — SSE stream drains, None sentinel sent, stream closes
```

---

## 4. Component: MySQL Registry

### 4.1 Role

`fab_semantic.mcp_servers` (MySQL) is the live source of truth for MCP server topology. `hub_server.py` queries this table on startup and caches results for 60 seconds. The agent never reads the registry directly — it calls `POST /discover` and receives a single server config. Tool discovery always happens at runtime via the MCP protocol.

`hub_service/mcp-hub.json` is the **seed source only** — run `python scripts/seed_hub_db.py` once to populate MySQL from it. After seeding, the JSON file is not read at runtime.

### 4.2 Table Schema

```sql
CREATE TABLE mcp_servers (
    id           VARCHAR(100)  NOT NULL,   -- stable identifier (e.g. "weather-server")
    name         VARCHAR(255)  NOT NULL,   -- human-readable display name
    endpoint     VARCHAR(500)  NOT NULL,   -- full URL of the MCP server
    transport    VARCHAR(50)   NOT NULL DEFAULT 'streamable-http',  -- "streamable-http" (all servers)
    capability   TEXT,                    -- domain label for fast routing signal (e.g. "FAB banking deal pricing and compliance")
    skills       JSON,                    -- list of specific operations; matched against query intent
    description  TEXT,                    -- full-context description shown to LLM routing agent
    examples     JSON,                    -- sample queries for few-shot routing signal
    start_cmd    TEXT,                    -- how to start this server (documentation)
    is_active    TINYINT(1)    NOT NULL DEFAULT 1,
    created_at   TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
);
```

### 4.3 Field Usage

| Field | Used by | Purpose |
|-------|---------|---------|
| `id` | hub_server.py routing + events | Server identifier in routing response and all events |
| `endpoint` | agent.py mcp_session() | URL passed to streamablehttp_client |
| `transport` | agent.py mcp_session() | Always `streamable-http` — all servers use /mcp/ |
| `capability` | `_build_server_context()` → routing message | Single-sentence domain label — primary routing signal; matched first |
| `skills` | `_build_server_context()` → routing message | Specific operations the server handles; used for precise intent matching |
| `description` | `_build_server_context()` → routing message | Full context paragraph; used to resolve ambiguity between servers |
| `examples` | `_build_server_context()` → routing message | Up to 2 sample queries; few-shot signal for routing |
| `start_cmd` | Documentation only | Shown in RUNBOOK.md |
| `is_active` | load_hub() WHERE clause | Excludes decommissioned servers without deleting rows |

**Routing signal hierarchy:** The agent prompt instructs the LLM to match `capability` and `skills` first (fast, precise), then use `description` and `examples` to resolve ambiguity.

**Tool discovery:** `load_mcp_tools(session)` is called after every MCP connection. Hub never stores tool names — they are discovered live from the server.

### 4.4 Current Servers (v3.0)

| id | Port | Transport | Capability |
|----|------|-----------|------------|
| weather-server | 8001 | streamable-http | Real-time weather and climate data |
| calculator-server | 8002 | streamable-http | Mathematical computation and unit conversion |
| data-server | 8003 | streamable-http | Geographic and currency reference data |
| fab-customer-server | 9100 | streamable-http | FAB banking customer intelligence |
| fab-pricing-server | 9200 | streamable-http | FAB banking deal pricing and compliance |

---

## 5. Component: agent.py

### 5.0 Authentication Architecture

The agent participates in authentication in **two directions** and uses the **same JWKS endpoint**
(`GET /.well-known/jwks.json`) that MCP servers use.

```
┌─ DIRECTION 1: OUTBOUND ─────────────────────────────────────────────────────────┐
│                                                                                  │
│  Step 1a  Agent ──username+password──► POST /auth/login                         │
│           Hub validates credentials (PBKDF2) → mints RS256 JWT (exp=8h)         │
│                                                                                  │
│  Step 1b  Agent ──Bearer <hub JWT>──► POST /discover                            │
│           Hub validates JWT (RS256 sig + iss + exp + role=agent)                │
│                                                                                  │
│  Step 1c  Agent ──Bearer <server JWT, aud=server_id>──► MCP Server POST /mcp   │
│           MCP validates via FastMCP JWTVerifier + BearerClaimsMiddleware (JWKS) │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘

┌─ DIRECTION 2: INBOUND VERIFICATION ─────────────────────────────────────────────┐
│  The agent verifies tokens it RECEIVES from the hub before using them.           │
│  Uses the same JWKS endpoint and PyJWKClient as MCP servers.                     │
│                                                                                  │
│  Step 2a  Hub ──access_token──► _get_hub_token()                                │
│           _verify_hub_token(token)                                               │
│           • PyJWKClient fetches /.well-known/jwks.json (cached 5 min)           │
│           • Checks: RS256 sig · iss=fab-mcp-hub · exp                           │
│           • Hard fail (raise) → token NOT cached; login rejected                 │
│           • Soft fail (JWKS down) → warning; token cached anyway                │
│                                                                                  │
│  Step 2b  Hub ──server_token[]──► run_agent()  (after POST /discover)           │
│           _verify_hub_token(token, audience=server_id)                           │
│           • Checks: RS256 sig · iss · aud=server_id · exp                       │
│           • Hard fail (raise) → that server is SKIPPED entirely                 │
│           • Soft fail (JWKS down) → warning; MCP server validates as fallback   │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘

OBSERVABILITY: _decode_jwt_claims() — verify_signature=False decode for logging/UI only.
               Never used for access-control; clearly labelled in code.
```

**MCP Server side** (`datalayer-as-service/mcp_server/auth.py`):

```
Incoming JWT (from agent) → FastMCP JWTVerifier (primary, 401 on fail)
                          → BearerClaimsMiddleware (defense-in-depth, own JWKS verify)
                          → _request_claims ContextVar
                          → require_role() inside tool function
```

### 5.1 Function Reference

#### Authentication helpers (all in `agent.py`)

| Function | Signature | Direction | Purpose |
|----------|-----------|-----------|---------|
| `_get_hub_token` | `async _get_hub_token() -> str` | OUTBOUND (1a) | POST /auth/login → hub JWT; caches result; calls `_verify_hub_token` on the returned token (Step 2a). |
| `_verify_hub_token` | `async _verify_hub_token(token, *, audience) -> dict` | INBOUND (2a/2b) | Verifies an RS256 JWT from the hub via `/.well-known/jwks.json`. Hard-raises on signature/aud/iss/exp mismatch. Soft-warns on JWKS outage. |
| `_jwks` | `_jwks() -> PyJWKClient \| None` | — | Lazy-init module-level `PyJWKClient` (keys cached 5 min). Shared across all `_verify_hub_token` calls. |
| `_decode_jwt_claims` | `_decode_jwt_claims(token) -> dict` | OBSERVABILITY ONLY | Unverified `verify_signature=False` decode for logging and chat UI display only. Never used for access-control. |
| `_auth_headers` | `_auth_headers(key) -> dict` | OUTBOUND (1b/1c) | Returns `{"Authorization": "Bearer <key>"}` when key is set; empty dict otherwise. |

#### Orchestration helpers

| Function | Signature | Purpose |
|----------|-----------|---------|
| `mcp_session` | `async mcp_session(server)` | Async context manager. Inspects `server["transport"]` and opens `sse_client` or `streamablehttp_client` with auth headers. Yields an initialized `ClientSession`. |
| `_fmt` | `_fmt(obj, limit=200) -> str` | Compact one-line JSON repr of an object, truncated to `limit` chars. Used for terminal log lines. |
| `_fetch_mcp_context` | `async _fetch_mcp_context(session, query, server_id, on_event) -> (prompt_messages, resource_context)` | Discovers prompts + resources via the live MCP session. Matches the query to a prompt template by keyword; extracts CUST/DEAL IDs from the query; reads static reference resources automatically. Returns structured messages + context text. |
| `_run_on_server` | `async _run_on_server(server, query, on_event) -> str` | Connects to one MCP server, discovers tools + prompts + resources (via `_fetch_mcp_context`), runs a ReAct loop with enriched context, returns the answer string. |
| `run_agent` | `async run_agent(query, on_event=None) -> str` | Orchestrates the full flow: POST /discover → JWKS-verify each server token (Step 2b) → for each valid server: mcp_session → load_mcp_tools → `_fetch_mcp_context` → create_react_agent → astream_events. |

#### MCP Prompt + Resource integration (`_fetch_mcp_context`)

```
MCP ClientSession (already open)
        │
        ├─ session.list_prompts()  → catalogue of prompt templates on this server
        │                            e.g. analyze_deal_pricing(customer_id, deal_id?)
        │
        ├─ keyword match + ID extraction (regex: CUST\d+, DEAL\d+)
        │    "exception" / "policy"    → review_policy_exceptions
        │    "competitor" / "compare"  → pricing_competitor_strategy
        │    "pricing" / "recommend"   → analyze_deal_pricing  (default)
        │
        ├─ session.get_prompt(name, {customer_id, deal_id})
        │    → PromptMessage list (role=user, content=structured analysis task)
        │    → converted to HumanMessage / AIMessage for LangGraph
        │
        ├─ session.list_resources() → catalogue of addressable reference docs
        │    pricing://policy/rules           (static policy text)
        │    pricing://guide/competitor-actions (MATCH/COUNTER/ESCALATE/REJECT guide)
        │    pricing://benchmarks/segments     (live DB table)
        │
        └─ session.read_resource(uri) for each URI containing "policy" / "guide"
             → text appended to system_prompt as reference documentation
             → dynamic resources (benchmarks) are NOT auto-read to avoid latency

Result used in create_react_agent():
   prompt=system_prompt + resource_context   ← reference docs injected here
   initial_messages=prompt_messages           ← structured task (or raw query fallback)
```

### 5.2 Routing Design (hub_server.py)

Routing logic lives entirely in `hub_server.py`. `agent.py` does not perform routing; it calls `POST /discover` and receives a list of matched server configs.

#### LLM Routing Agent

`hub_server.py` runs a per-request ReAct routing agent (LangGraph `create_react_agent`) with a single tool:

| Tool | Purpose |
|------|---------|
| `pick_server(server_id, reason)` | Records a routing decision. The agent may call this once (focused query) or multiple times (multi-domain query) |

**Why single tool, not two:** llama3.2:3b would inconsistently call a hypothetical `get_server_list()` tool. Removing it and injecting the server list directly into the message eliminates that failure mode.

**Per-request tool factory (`_make_routing_tools`):** Each `/discover` call creates fresh tools via closure so concurrent requests cannot corrupt each other's routing state. The LLM singleton (`_get_llm()`) is still cached.

The agent receives all server configs pre-formatted in the message (via `_build_server_context`), reasons over `Capability` and `Skills`, then calls `pick_server()` for each server it selects. Every routing step is logged to terminal via `astream_events`.

Method name in response: `"agent"` (or `"first_match"` when `HUB_LLM_ENABLED=false`)

Example routing decisions:
- "What is the capital of France?" → `data-server` (geography/timezone domain)
- "Convert 100 miles to kilometres" → `calculator-server` (math/unit conversion domain)
- "Current temperature in Tokyo" → `weather-server` (weather domain)
- "Get the 360 profile for CUST007" → `fab-customer-server` (customer intelligence domain)
- "Show pricing recommendation for DEAL001" → `fab-pricing-server` (pricing engine domain)

When `HUB_LLM_ENABLED=false`, the hub skips the agent and returns the first registered server with method `"first_match"`.

### 5.3 Agentic Tool Loop (inside run_agent) — LangGraph create_react_agent

```python
# 1. Fresh tool discovery from live MCP server
tools = await load_mcp_tools(session)          # langchain-mcp-adapters

# 2. Build agent with tools bound at creation time
agent = create_react_agent(llm, tools=tools, prompt="You are a helpful assistant...")

# 3. Stream events — captures every tool call, result, and LLM turn
async for event in agent.astream_events({"messages": [HumanMessage(content=query)]}, version="v2"):
    if event["event"] == "on_tool_start": ...   # log tool call
    elif event["event"] == "on_tool_end": ...   # log tool result
    elif event["event"] == "on_chat_model_end": # capture final answer
        output = event["data"].get("output")
        if output and not getattr(output, "tool_calls", []):
            answer = output.content
```

`load_mcp_tools(session)` (from `langchain-mcp-adapters`) converts the live MCP session's tool list into LangChain `BaseTool` instances. `create_react_agent` builds a LangGraph ReAct agent with those tools; the agent must be built inside the active MCP session context. `astream_events` streams every reasoning and tool call step in real time, providing full traceability in terminal logs.

---

## 6. Component: chat_service/chat_server.py

### 6.1 Role

FastAPI application on port 8080. Provides:
1. A browser-accessible single-page app (HTML/CSS/JS all inline in one GET response)
2. A real-time SSE endpoint that executes queries and streams events to the browser
3. A health endpoint

### 6.2 Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Returns the complete SPA: HTML structure + CSS styles + JavaScript event handling, all in one response. No static file serving needed. |
| POST | `/chat/stream` | Accepts `{"query": "..."}` JSON body. Returns `text/event-stream` SSE. |
| GET | `/health` | Returns `{"status": "ok", "model": "llama3.2:3b", "hub": "http://localhost:8090"}` |

### 6.3 SSE Architecture — Queue + Sentinel Pattern

The `/chat/stream` endpoint uses an `asyncio.Queue` to bridge the agent's event callback with the HTTP response stream:

```
POST /chat/stream
│
├── creates: queue = asyncio.Queue()
│
├── defines: async on_event(event):
│               await queue.put(event)     ← agent pushes events here
│
├── defines: async run_with_sentinel():
│               await run_agent(query, on_event)
│               await queue.put(None)      ← None = sentinel (end of stream)
│
├── asyncio.create_task(run_with_sentinel())  ← agent runs concurrently
│
└── async event_generator():              ← reads from queue, yields SSE
        while True:
            event = await queue.get()
            if event is None:
                break                     ← sentinel received, stop yielding
            yield f"data: {json.dumps(event)}\n\n"

return StreamingResponse(event_generator(), media_type="text/event-stream")
```

**Why Queue + sentinel:**
- Decouples the agent (producer) from the HTTP response (consumer) completely
- The asyncio.Queue is safe for concurrent producer/consumer in a single event loop
- None sentinel is unambiguous and avoids polling
- If the agent raises an exception, run_with_sentinel catches it, emits an error event, then puts None to close the stream cleanly

### 6.4 Browser-Side Rendering

The SPA JavaScript listens to the EventSource and renders each event type differently:
- `hub_loaded` — shows server count in a status bar
- `routing` — shows routing method (`LLM Agent` or `First Match`) and chosen server
- `mcp_connecting` / `mcp_connected` — shows connection status indicators
- `tool_call` — shows tool name and arguments in a collapsible card
- `tool_result` — shows result preview beneath the tool call
- `final_answer` — renders the full markdown answer
- `error` — renders a red error card

---

## 7. Component: MCP Servers

### 7.1 Demo Servers (datalayer-as-service/mcp_server/)

All three use `fastmcp` with **streamable-HTTP** transport (`mcp.http_app()`). They serve mock/static data and do not require a database.

| File | Port | Endpoint | Tools |
|------|------|----------|-------|
| `datalayer-as-service/mcp_server/weather_server.py` | 8001 | `http://localhost:8001/mcp/` | get_weather, get_forecast, get_historical_weather |
| `datalayer-as-service/mcp_server/calc_server.py` | 8002 | `http://localhost:8002/mcp/` | calculate, statistics, convert_units |
| `datalayer-as-service/mcp_server/data_server.py` | 8003 | `http://localhost:8003/mcp/` | get_country_info, get_currency_rate, get_timezone |

Start command:
```bash
python datalayer-as-service/mcp_server/weather_server.py 8001
python datalayer-as-service/mcp_server/calc_server.py 8002
python datalayer-as-service/mcp_server/data_server.py 8003
```

### 7.2 FAB Data Layer Servers (datalayer-as-service/mcp_server/)

Both use `fastmcp` with streamable-HTTP transport. They connect to MySQL 8.4 via SQLAlchemy + PyMySQL.

**Customer Intelligence Server** — port 9100

| Tool | Description |
|------|-------------|
| `customer_360` | Full customer profile: segment, revenue, risk rating, relationship summary |
| `profitability_summary` | Net interest income, fee income, ROE, cost-to-income by customer |
| `margin_analysis` | Spread analysis, margin trend, comparison to segment benchmark |
| `rwa_impact` | Risk-weighted assets, capital consumption, capital efficiency ratio |
| `win_loss_insights` | Deal win/loss history, win rate by product and segment |
| `credit_rating_events` | Rating agency events, internal rating changes, watchlist flags |
| `cross_sell_opportunity` | Product gap analysis, propensity scores, next-best-product |
| `relationship_discount` | Approved discount levels, discount utilisation, justification |
| `similar_customer_pricing` | Peer pricing benchmarks from anonymised similar customers |

Start command:
```bash
cd datalayer-as-service
MCP_TRANSPORT=http MCP_HOST=127.0.0.1 MCP_PORT=9100 python -m mcp_server.customer_server
```

**Pricing Engine Server** — port 9200

| Tool | Description |
|------|-------------|
| `pricing_recommendation` | AI-driven price recommendation with margin floor and ceiling |
| `new_customer_pricing` | Pricing model for prospective customers based on segment and risk |
| `competitor_price_analysis` | External benchmark prices by product and deal type |
| `pricing_trace` | Full audit trace of how a price was derived for a specific deal |
| `segment_pricing_benchmark` | Aggregated pricing statistics by segment and product |
| `operations_cost_impact` | Operational cost loading per transaction type |
| `policy_exception` | Policy exception requests: status, approvers, conditions |
| `non_compliant_deals` | Deals that breach minimum margin or policy rules |
| `compare_fab_vs_competitor` | Side-by-side deal economics: FAB pricing vs competitor offer |

**Prompts** — structured analysis workflow templates (MCP `prompts/list` + `prompts/get`):

| Prompt | Args | Description |
|--------|------|-------------|
| `analyze_deal_pricing` | `customer_id`, `deal_id?` | 5-step workflow: recommendation → trace → competitor → policy exceptions → summary |
| `review_policy_exceptions` | `customer_id?` | 4-step compliance audit: list non-compliant → exception details → risk ranking → actions |
| `pricing_competitor_strategy` | `customer_id?`, `deal_id?` | 5-step competitive response: gap analysis → benchmark → discount → ops cost → strategy |

**Resources** — reference documents addressable by URI (MCP `resources/list` + `resources/read`):

| URI | Type | Description |
|-----|------|-------------|
| `pricing://policy/rules` | Static text | FAB pricing policy: margin floors, discount caps, approval thresholds |
| `pricing://guide/competitor-actions` | Static text | MATCH/COUNTER/ESCALATE/REJECT decision criteria and `competitor_gap_bps` interpretation |
| `pricing://benchmarks/segments` | Dynamic (DB) | Live markdown table from `fab_semantic.segment_pricing_benchmark` |

Start command:
```bash
cd datalayer-as-service
MCP_TRANSPORT=http MCP_HOST=127.0.0.1 MCP_PORT=9200 python -m mcp_server.pricing_server
```

**Shared modules:**
- `tools.py` — All MySQL query functions + `_to_json()` serialiser, shared by all three servers
- `db.py` — SQLAlchemy engine singleton (`_engine` cached at module level); builds once on first call, reused across all tool calls. Reads `MYSQL_USER/PASSWORD` from `.env`. Never uses the agent JWT.

---

## 8. Event System

All events are Python dicts emitted via the `on_event` async callback. In the chat UI flow, `on_event` puts events into the `asyncio.Queue` for SSE delivery to the browser and saves them to the `chat_traces` MySQL table. Selected MCP lifecycle events are **also bridged to hub observability** — forwarded to `log_event()`, written to the `hub_events` table, and served by `GET /api/logs`. Events marked **→ /api/logs** below appear there. When agent.py is used programmatically (e.g., test_agent.py), `on_event` can be None and no events are emitted.

### 8.1 Event Reference (in order of emission)

| Event type | Emitted by | Key fields | Notes |
|------------|------------|------------|-------|
| `hub_loaded` | `run_agent` | `hub_name`, `server_ids` | First event of every request |
| `routing` | `run_agent` | `method`, `reason`, `server_id`, `server_ids` | `method` is `"agent"`, `"keyword"`, or `"first_match"`. `"keyword"` means LLM was unavailable and `_keyword_route()` matched by word score. `server_ids` is the full list; `server_id` is the primary (first) for UI |
| `mcp_connecting` | `run_agent` | `server_id`, `endpoint` | Before MCP transport opens. **→ /api/logs** |
| `mcp_connected` | `_run_on_server` | `server_id`, `tool_count`, `tool_names`, `prompt_count`, `has_resources` | After tools + capabilities discovered. **→ /api/logs** |
| `mcp_capabilities` | `_fetch_mcp_context` | `server_id`, `prompts[]`, `resources[]` | Emitted when server exposes ≥1 prompt/resource. **→ /api/logs** |
| `mcp_prompt_used` | `_fetch_mcp_context` | `server_id`, `prompt_name`, `prompt_args`, `message_count` | Structured prompt matched and applied. **→ /api/logs** |
| `tool_call` | `_run_on_server` | `server_id`, `tool_name`, `args` | Emitted on each `on_tool_start` astream event |
| `tool_result` | `_run_on_server` | `server_id`, `tool_name`, `result` | Emitted on each `on_tool_end` astream event |
| `final_answer` | `run_agent` | `content` | The complete answer string. Last content event. |
| `error` | `run_agent` | `message` | Agent failure. Stream still closes cleanly. **→ /api/logs** |

### 8.2 SSE Wire Format

Each event is serialised as:
```
data: {"type": "tool_call", "server_id": "fab-pricing-server", "tool_name": "pricing_recommendation", "args": {"customer_id": "CUST001"}}\n\n
```

The double newline `\n\n` is the SSE record separator. The browser EventSource fires `onmessage` for each record.

---

## 9. Single-Server Query — Sequence

```
chat_server.py          run_agent()                hub_server.py :8090       target MCP server      Ollama
      │                     │                               │                        │                  │
      │─ POST /chat/stream ►│                               │                        │                  │
      │                     │─ POST /discover ─────────────►│                        │                  │
      │                     │                               │  routing agent runs:   │                  │
      │                     │                               │  (server list injected │                  │
      │                     │                               │   into message)        │                  │
      │                     │                               │  pick_server(id,reason)│                  │
      │                     │◄─ {servers, method, reason} ──│                        │                  │
      │                     │  [hub_loaded event]           │                        │                  │
      │◄── SSE: hub_loaded ─│                               │                        │                  │
      │                     │  [routing event]              │                        │                  │
      │◄── SSE: routing ────│                               │                        │                  │
      │                     │─ mcp_session(server) ─────────────────────────────────►│                  │
      │                     │  [mcp_connecting event]        │                       │                  │
      │◄── SSE: mcp_conn ───│                               │                        │                  │
      │                     │─ initialize() ────────────────────────────────────────►│                  │
      │                     │─ load_mcp_tools() ────────────────────────────────────►│                  │
      │                     │◄─ tool list ──────────────────────────────────────────│                  │
      │                     │  [mcp_connected event]         │                       │                  │
      │◄── SSE: mcp_conn ───│                               │                        │                  │
      │                     │  create_react_agent + astream_events loop begins       │                  │
      │                     │─ chat(query + tool schemas) ──────────────────────────────────────────────►│
      │                     │◄─ tool_call: get_weather(city="Dubai") ───────────────────────────────────│
      │                     │  [tool_call event]             │                       │                  │
      │◄── SSE: tool_call ──│                               │                        │                  │
      │                     │─ call_tool() ─────────────────────────────────────────►│                  │
      │                     │◄─ result ─────────────────────────────────────────────│                  │
      │                     │  [tool_result event]           │                       │                  │
      │◄── SSE: tool_res ───│  ...loop continues if needed...│                       │                  │
      │                     │─ chat(tool result) ───────────────────────────────────────────────────────►│
      │                     │◄─ final text (no tool_calls) ─────────────────────────────────────────────│
      │                     │  [final_answer event]          │                       │                  │
      │◄── SSE: final_ans ──│                               │                        │                  │
      │◄── SSE stream end ──│  (None sentinel)              │                        │                  │
```

---

## 10. Transport Protocols

All five MCP servers use **streamable-HTTP** transport. The `mcp_session()` async context manager in agent.py uses `streamablehttp_client` for all connections.

### 10.1 Transport

All servers are configured with `mcp.http_app(middleware=claims_middleware())` — no `transport=` argument, which defaults to streamable-HTTP.

| Aspect | Value (all servers) |
|--------|---------------------|
| MCP client | `mcp.client.streamable_http.streamablehttp_client` |
| Endpoint path | `/mcp/` |
| Underlying protocol | HTTP POST with chunked response |
| FastMCP server config | `mcp.http_app()` (default = streamable-HTTP) |
| Auth middleware | FastMCP `JWTVerifier` + `BearerClaimsMiddleware` (all 5 servers) |

> **Note:** SSE transport (`/sse`) was removed from the demo servers in v2.1. The MCP Python client SDK had a TaskGroup protocol incompatibility with FastMCP's SSE implementation that caused connection errors. All servers now use streamable-HTTP.

### 10.2 MCP Protocol Sequence (same for both transports)

```
Client (agent.py)              Server (MCP server)
      │                               │
      │── initialize ────────────────►│
      │   {protocolVersion,           │
      │    clientInfo}                │
      │                               │
      │◄── InitializeResult ──────────│
      │   {protocolVersion,           │
      │    capabilities}              │
      │                               │
      │── tools/list ────────────────►│
      │                               │
      │◄── [{name, description,       │
      │      inputSchema}]            │
      │                               │
      │── tools/call ────────────────►│
      │   {name, arguments}           │
      │                               │
      │◄── {content:                  │
      │     [{type:"text", text:"…"}]}│
      │                               │
      │  (repeated for each tool call │
      │   in the agentic loop)        │
```

`mcp.ClientSession` manages the initialize handshake automatically. The agent code only calls `list_tools()` and `call_tool()`.

---

## 11. MySQL Schema

### 11.1 Overview

Database: `fab_semantic` (MySQL 8.4)
Connection: SQLAlchemy + PyMySQL, credentials in `datalayer-as-service/.env`

Architecture: 14 curated tables in schema `fab_curated` → 16 semantic views in schema `fab_semantic`

```
datalayer-as-service/data/raw/       (14 source CSVs)
         │
         │  02_create_curated_data.py
         │  03_load_curated_to_mysql.py
         ▼
fab_curated schema (14 tables)
  customers, deals, products, segments, ratings,
  win_loss, cross_sell, benchmarks, competitors,
  policy_exceptions, non_compliant_deals,
  operations_costs, relationship_discounts,
  similar_customers
         │
         │  04_create_semantic_views.py
         ▼
fab_semantic schema (16 views)
  — business-friendly column names, pre-joined, denormalised
  — MCP tools query exclusively against these views
```

### 11.2 Tool-to-View Mapping

**Customer Intelligence Tools (customer_server.py port 9100)**

| Tool | Primary View(s) queried |
|------|------------------------|
| `customer_360` | `fab_semantic.v_customer_360` |
| `profitability_summary` | `fab_semantic.v_profitability` |
| `margin_analysis` | `fab_semantic.v_margin_analysis` |
| `rwa_impact` | `fab_semantic.v_rwa_impact` |
| `win_loss_insights` | `fab_semantic.v_win_loss` |
| `credit_rating_events` | `fab_semantic.v_credit_ratings` |
| `cross_sell_opportunity` | `fab_semantic.v_cross_sell` |
| `relationship_discount` | `fab_semantic.v_relationship_discounts` |
| `similar_customer_pricing` | `fab_semantic.v_similar_customers` |

**Pricing Engine Tools (pricing_server.py port 9200)**

| Tool | Primary View(s) queried |
|------|------------------------|
| `pricing_recommendation` | `fab_semantic.v_pricing_recommendations` |
| `new_customer_pricing` | `fab_semantic.v_segment_benchmarks` |
| `competitor_price_analysis` | `fab_semantic.v_competitor_analysis` |
| `pricing_trace` | `fab_semantic.v_deal_pricing_trace` |
| `segment_pricing_benchmark` | `fab_semantic.v_segment_benchmarks` |
| `operations_cost_impact` | `fab_semantic.v_operations_costs` |
| `policy_exception` | `fab_semantic.v_policy_exceptions` |
| `non_compliant_deals` | `fab_semantic.v_non_compliant_deals` |
| `compare_fab_vs_competitor` | `fab_semantic.v_competitor_analysis`, `v_deal_pricing_trace` |

### 11.3 SQL Files

| File | Contents |
|------|----------|
| `sql/01_create_schemas.sql` | Creates `fab_curated` and `fab_semantic` schemas |
| `sql/02_create_curated_tables.sql` | Creates all 14 curated tables |
| `sql/03_create_semantic_views.sql` | Creates all 16 semantic views |

---

## 12. Routing Decision Tree (hub_server.py — `route_to_server()`)

```
                        Query arrives at POST /discover
                                       │
                                       ▼
                         ┌─────────────────────────────┐
                         │  HUB_LLM_ENABLED = true?    │
                         └─────────────────────────────┘
                                       │
                         ┌── enabled? ─┤
                         │ YES         │ NO
                         ▼             ▼
              ┌──────────────────┐  ┌─────────────────────────┐
              │  LLM routing     │  │  _keyword_route()        │
              │  ReAct agent     │  │  score servers by word   │
              │  (per-request)   │  │  match (≥4 chars) vs     │
              │  pick_server ×N  │  │  id/cap/desc/skills/ex   │
              └──────────────────┘  └─────────────────────────┘
                         │                      │
              agent calls pick_server()    best-score server
                         │                      │
              ┌── any valid ids? ─┐             │
              │ YES               │ NO          │
              ▼                   ▼             ▼
     Return matched     ┌────────────────────────────┐
     servers list       │  _keyword_route() fallback  │
     method="agent"     │  score servers by word match│
                        └────────────────────────────┘
                                   │
                        ┌── match found? ──┐
                        │ YES              │ NO
                        ▼                  ▼
              Return best match    Return [servers[0]]
              method="keyword"     method="agent"
                                   (logs WARNING)
```

**`_keyword_route()` algorithm** (`hub_server.py`):
1. Splits intent into words ≥4 characters (noise-filters short words)
2. Builds a corpus string per server: `id + capability + description + skills + examples`
3. Scores each server by count of query words found in its corpus
4. Returns the highest-scoring server ID (ties: first wins)

This ensures correct routing even when Ollama is unreachable or LLM routing is disabled.

**Routing performance:**
- LLM agent: 200–800ms round-trip to Ollama llama3.2:3b; every step logged via `astream_events`
- LLM failed / disabled + keyword match: < 1ms; logs `[hub] keyword: ...`
- LLM failed + no keyword match: < 1ms, returns `servers[0]` (logs WARNING)
- Multi-server result: `agent.py` runs `asyncio.gather` for parallel tool loops

---

## 13. Authentication and Authorisation

### 13.1 Communication Paths

```
agent.py  ──[HUB_API_KEY Bearer]──►  hub_server.py    hub_service/auth.py
agent.py  ──[MCP_API_KEY Bearer]──►  MCP servers      mcp_server/auth.py
```

Each layer has an independent auth module with identical provider model: static API key, local HS256 JWT, or Azure AD RS256 JWT. Both default to **disabled** (open dev mode).

### 13.2 Hub Auth (`hub_service/auth.py`)

Wired into FastAPI via `_require_auth` dependency on protected endpoints (`/servers`, `/discover`).

| Variable | Default | Purpose |
|----------|---------|---------|
| `AUTH_ENABLED` | `false` | `true` enables token validation |
| `AUTH_PROVIDER` | `local` | `local` or `azure` |
| `HUB_API_KEY` | *(empty)* | Static Bearer secret (local) |
| `JWT_SECRET` | *(empty)* | HS256 signing secret (local JWT) |
| `AZURE_TENANT_ID` | *(empty)* | Tenant UUID (azure) |
| `AZURE_CLIENT_ID` | *(empty)* | Token audience (azure) |

**Open dev mode:** `AUTH_ENABLED=true` but neither `HUB_API_KEY` nor `JWT_SECRET` set → all requests pass. Prevents accidental lockout during initial setup.

**Internal helpers** (not part of public API):
- `_normalize_claims(payload)` — converts JWT payload to consistent `{sub, roles, iss, aud, server_id, _source}` dict; used by both RS256 and HS256 paths
- `_jwt_error_result(exc)` — maps PyJWT exception to `(False, {"_error": ...})` tuple; shared by both verify paths

**Token generation:** `python hub_service/auth.py --sub agent --hours 24`

### 13.3 MCP Server Auth (`datalayer-as-service/mcp_server/auth.py`)

All MCP servers validate incoming RS256 JWTs issued by the hub. Two auth paths:

**Path A — FAB data servers** (`customer_server.py`, `pricing_server.py`, `server.py`):

| Layer | Component | Role |
|-------|-----------|------|
| Primary | FastMCP `JWTVerifier` (via `build_jwt_verifier()`) | Validates RS256 sig + `aud=MCP_SERVER_ID` |
| Secondary | `BearerClaimsMiddleware` | Independent JWKS re-verification; populates `_request_claims` ContextVar |
| RBAC | `require_role()` inside each tool | Reads claims from ContextVar |

**Path B — streamable-HTTP demo servers** (`calc_server.py`, `weather_server.py`, `data_server.py`):

Same dual-middleware pattern as Path A (migrated from SSE in v2.1):

| Layer | Component | Role |
|-------|-----------|------|
| Primary | FastMCP `JWTVerifier` (via `build_jwt_verifier()`) | Validates RS256 sig + `aud=MCP_SERVER_ID` |
| Secondary | `BearerClaimsMiddleware` (via `claims_middleware()`) | Independent JWKS re-verification; populates `_request_claims` ContextVar |
| RBAC | `require_role()` inside each tool | Reads claims from ContextVar |

| Env variable | Default | Purpose |
|-------------|---------|---------|
| `MCP_AUTH_ENABLED` | `true` | `false` disables all token checks (dev mode) |
| `MCP_SERVER_ID` | *(empty)* | Audience claim enforced by `JWTVerifier` |
| `HUB_JWKS_URL` | hub + `/.well-known/jwks.json` | JWKS endpoint for RS256 key fetch |
| `HUB_JWT_ISSUER` | `fab-mcp-hub` | Issuer enforced during verification |

Token failure tiers:
- JWKS signature / audience / issuer mismatch → **hard 401** (hard fail)
- JWKS endpoint unreachable → **WARNING + soft fail** (request proceeds unverified; defense-in-depth only)

### 13.4 Local JWT Workflow

```
                  jwt.encode (hub_service/auth.py or mcp_server/auth.py)
JWT_SECRET ─────►────────────────────────────────────────────► JWT token
                                                                    │
                              set as HUB_API_KEY / MCP_API_KEY ◄───┘
                                          │
agent.py sends: Authorization: Bearer <JWT>
                                          │
                          verify_token() / verify_mcp_token() decodes with same secret
```

### 13.5 Azure AD Migration Path

Both modules implement the same migration pattern:

1. Register an app in Azure Entra ID → copy Tenant ID and Client ID.
2. Configure the caller (`agent.py` or any HTTP client) to obtain tokens via `client_credentials` flow from Azure AD and store them in `HUB_API_KEY` / `MCP_API_KEY`.
3. Set `AUTH_PROVIDER=azure`, `AZURE_TENANT_ID`, `AZURE_CLIENT_ID` (hub) and/or `MCP_AUTH_PROVIDER=azure`, `MCP_AZURE_TENANT_ID`, `MCP_AZURE_CLIENT_ID` (MCP servers).
4. Remove static key and JWT secret env vars.

Azure RS256 validation uses PyJWKClient to fetch JWKS from `https://login.microsoftonline.com/<tenant>/discovery/v2.0/keys`, matches the key by `kid`, then verifies signature, issuer, and audience. Requires `cryptography` package (included in `requirements.txt`).

### 13.6 Authorization Model

Authorization in the Simple Hub is binary: a valid token grants access to all endpoints and all MCP tools. Per-tool or per-resource authorization is a Full Hub concern (out of scope here). In Azure, RBAC via Entra ID app roles can be layered on top of authentication at a later stage.

---

## 14. Sample Queries and MCP Capability Map

The table below lists the eight "Quick Queries" surfaced in the Chat UI dashboard and on the `start_servers.sh` CLI banner. Each query is designed to exercise a specific MCP capability so a demo audience can see the full feature set without typing free-form questions.

### 14.1 Quick Query Catalogue

| # | Query | Target Server | MCP Capability Used | Notes |
|---|-------|--------------|---------------------|-------|
| 1 | `Give me a comprehensive pricing analysis for CUST001 and DEAL003` | `fab-pricing-server` | **Prompt** — `analyze_deal_pricing(CUST001, DEAL003)` | 5-step structured workflow: recommendation → price trace → competitor gap → compliance → summary |
| 2 | `Review all policy exceptions for CUST002 and recommend actions` | `fab-pricing-server` | **Prompt** — `review_policy_exceptions(CUST002)` | 4-step compliance audit: list exceptions → deal details → risk ranking → escalation actions |
| 3 | `Build a competitor pricing strategy for CUST003 on DEAL007` | `fab-pricing-server` | **Prompt** — `pricing_competitor_strategy(CUST003, DEAL007)` | 5-step competitive response: gap analysis → segment benchmark → discount headroom → ops cost → strategy |
| 4 | `Walk me through the step-by-step price build for DEAL040` | `fab-pricing-server` | **Tool** — `pricing_trace(customer_id, DEAL040)` | Exposes every component: base rate → credit spread → RWA → ops margin → approved price |
| 5 | `Which deals are non-compliant and why?` | `fab-pricing-server` | **Tool** — `non_compliant_deals(customer_id)` | Admin-scoped tool; returns all deals breaching margin floors or discount caps with policy rule references |
| 6 | `What are the pricing benchmarks for the Corporate segment?` | `fab-pricing-server` | **Tool** — `segment_pricing_benchmark(Corporate, …)` + **Resource** — `pricing://benchmarks/segments` | Tool returns live DB data; resource is loaded as reference context for the LLM |
| 7 | `Show me the 360 profile for CUST001` | `fab-customer-server` | **Tool** — `customer_360(CUST001)` | Customer intelligence: profile, profitability, RWA, win/loss, credit rating events |
| 8 | `What MCP servers are available?` | Hub server | **Hub REST API** — `GET /discover?intent=…` | Routes to hub; returns registry of all active MCP servers and their capabilities |

### 14.2 MCP Capability Coverage

| MCP Feature | Queries that exercise it | Description |
|-------------|--------------------------|-------------|
| **Prompts** | 1, 2, 3 | Pre-defined multi-step workflows registered by the MCP server; `_fetch_mcp_context()` loads them as system messages before the tool-call loop |
| **Resources** | 1, 2, 3, 6 | Static or dynamic reference data (`pricing://policy/rules`, `pricing://guide/competitor-actions`, `pricing://benchmarks/segments`) injected as resource context |
| **Tools** | 1–7 | All 9 pricing tools and 9 customer tools; discovered at runtime via `load_mcp_tools(session)` |
| **Multi-step agent loop** | 1, 2, 3, 7 | LangGraph `create_react_agent` chains multiple tool calls to build a complete answer |
| **Multi-server fan-out** | — | Queries referencing both CUST and DEAL concepts may route to both servers; synthesis merges results |

### 14.3 How Prompts and Resources Are Used

When `use_context=True` (the default), `_fetch_mcp_context()` in `agent.py` does the following before the tool-call loop:

1. **Lists prompts** — calls `session.list_prompts()` on the target MCP server.
2. **Selects the best prompt** — picks the prompt whose name best matches the query intent (e.g., `"analysis"` → `analyze_deal_pricing`).
3. **Gets prompt messages** — calls `session.get_prompt(name, arguments={...})` with customer/deal IDs extracted from the query.
4. **Lists resources** — calls `session.list_resources()`.
5. **Reads each resource** — calls `session.read_resource(uri)` for each registered resource URI.
6. **Injects into context** — prompt messages become the first `HumanMessage` entries; resource text is appended to the system-level context string passed to `create_react_agent`.

This means queries 1–3 automatically receive a structured, multi-step instruction set from the server itself — the LLM does not have to infer the analysis workflow from the query alone.

Pass `--no-context` to `agent.py` to skip prompts and resources and use tools only:

```bash
python agent.py --no-context "Give me a comprehensive pricing analysis for CUST001"
```

---

## 15. Tech Stack

| Package | Version | Purpose |
|---------|---------|---------|
| Python | 3.13.x | Runtime |
| mcp | >=1.9.2 | MCP client: ClientSession, sse_client, streamablehttp_client |
| fastmcp | 2.3.4 | Server-side MCP framework (all 5 MCP servers use this) |
| pydantic | 2.11.3 | Pinned — pydantic 2.13+ breaks fastmcp 2.3.4 |
| openai | >=1.0.0 | Ollama client via OpenAI-compatible API |
| langchain-openai | >=0.2.0 | LangChain OpenAI-compat client; provides `ChatOpenAI` for both hub routing agent and run_agent |
| langchain-mcp-adapters | >=0.1.0 | Converts MCP tools to LangChain BaseTool instances (`load_mcp_tools`) |
| langgraph | >=0.2.0 | `create_react_agent` + `astream_events` used in hub routing agent and run_agent |
| fastapi | >=0.111.0 | chat_server.py and hub_server.py HTTP framework |
| uvicorn[standard] | 0.34.0 | ASGI server for chat_server.py and hub_server.py |
| sqlalchemy | >=2.0.0 | ORM / connection pool for FAB data layer servers |
| pymysql | >=1.1.0 | MySQL driver (used by SQLAlchemy) |
| PyJWT | >=2.0.0 | JWT encode/decode for local HS256 auth and Azure AD RS256 validation |
| cryptography | >=41.0.0 | RSA key parsing for Azure AD RS256 JWT verification |
| Ollama (runtime) | latest | Local LLM runtime, exposes OpenAI-compatible API at :11434 |
| llama3.2:3b (model) | 3b params | Local LLM for hub routing agent and tool-call ReAct loop |
| MySQL | 8.4 | Database for FAB data layer (16 views, 14 tables) |

**No cloud API key is required.** All LLM calls go to `http://localhost:11434/v1`.

---

## 16. Design Decisions

### Why a JSON file as the hub registry?

The original requirement was "a simple JSON file as MCP hub." JSON gives:
- Zero infrastructure — no database, no API gateway, no schema migration
- Human-readable, diff-able, version-controlled server registry
- Trivial to add or remove servers without changing agent code
- Clear separation of concerns: JSON describes *what exists*, agent code decides *what to call*

A new server is added by adding one JSON object to `servers[]`. On the next agent invocation it is automatically included in routing.

### Why Ollama llama3.2:3b as the local LLM?

The system runs 100% locally. No cloud API key, no internet dependency, no usage cost. llama3.2:3b is small enough (2 GB) to run on a developer laptop with acceptable latency. The routing task is simple — the model reads short server descriptions and returns a small JSON object. The agentic tool loop task is also manageable for 3b parameters when tools are well-described.

If the model returns malformed JSON, the routing fallback extracts a `server_id` string using regex over the raw response.

### Why discover tools at runtime?

`load_mcp_tools(session)` is called after every MCP connection. This means:
- Tool additions to a server do not require any registry updates (neither JSON nor MySQL)
- The LLM receives actual parameter schemas from the server, not approximations
- Hub routing is purely description-based — it never needs to know tool names

### Why an agentic loop instead of a single tool call?

Some queries require multiple tools in sequence — for example: get customer profile, then look up their deals, then get pricing for those deals. The loop lets the LLM chain tool calls, accumulating context with each result before deciding whether to call another tool or produce a final answer.

### Why split the original server into customer_server and pricing_server?

The original `server.py` contained all 18 tools. Splitting into two focused servers:
- Enables independent routing — customer queries never load pricing tools and vice versa
- Reduces LLM context size — each server presents 9 tool schemas, not 18, improving tool selection accuracy
- Supports independent scaling and deployment

### Why pydantic is pinned to 2.11.3?

fastmcp 2.3.4 has an incompatibility with pydantic 2.13+. The pinned version ensures the server-side MCP framework works correctly. This is a transitive dependency constraint — if fastmcp is upgraded, the pydantic pin can be revisited.

---

## 17. File Map

```
fab-mcp-hub-simple/
├── agent.py                  ← Orchestrator: POST /discover → mcp_session → load_mcp_tools → create_react_agent
├── test_agent.py             ← Integration tests
├── requirements.txt          ← Python dependencies (all pinned or bounded)
├── RUNBOOK.md                ← Operations guide: start order, health checks, troubleshooting
├── TECHNICAL_DESIGN.md       ← This document
├── README.md                 ← Quick start
│
├── hub_service/              ← Hub server (REST routing/discovery API)
│   ├── hub_server.py         ← FastAPI REST discovery API (port 8090); LLM routing agent; multi-server routing
│   ├── auth.py               ← Auth module: AUTH_ENABLED/AUTH_PROVIDER flags, local JWT, Azure AD stub
│   ├── db.py                 ← SQLAlchemy engine for fab_semantic (reads datalayer-as-service/.env)
│   └── mcp-hub.json          ← Seed source only (run scripts/seed_hub_db.py once; not read at runtime)
│
├── chat_service/             ← Chat UI server
│   └── chat_server.py        ← FastAPI + SSE + HTML/CSS/JS SPA on port 8080
│
├── datalayer-as-service/     ← Demo + FAB banking data layer (MySQL-backed for FAB servers)
│   ├── mcp_server/
│   │   │   ├── auth.py            ← MCP server auth: MCP_AUTH_ENABLED/MCP_AUTH_PROVIDER, local JWT + Azure stub
│   │   ├── weather_server.py  ← Demo — port 8001, streamable-HTTP /mcp/, mock weather for 10 cities
│   │   ├── calc_server.py     ← Demo — port 8002, streamable-HTTP /mcp/, math / statistics / unit conversion
│   │   ├── data_server.py     ← Demo — port 8003, streamable-HTTP /mcp/, countries / currencies / timezones
│   │   ├── customer_server.py ← port 9100, streamable-HTTP, 9 customer intelligence tools
│   │   ├── pricing_server.py  ← port 9200, streamable-HTTP, 9 pricing engine tools
│   │   ├── tools.py           ← MySQL query functions shared by both servers
│   │   └── db.py              ← SQLAlchemy engine + PyMySQL pool, reads from .env
│   ├── sql/
│   │   ├── 01_create_schemas.sql       ← creates fab_curated and fab_semantic schemas
│   │   ├── 02_create_curated_tables.sql ← 14 curated tables
│   │   └── 03_create_semantic_views.sql ← 16 semantic views
│   ├── data/
│   │   ├── raw/              ← 14 source CSV files (as received)
│   │   └── curated/          ← 14 cleaned / normalised CSV files
│   ├── .env                  ← MySQL credentials (DB_HOST, DB_USER, DB_PASSWORD, DB_NAME)
│   ├── 01_validate_raw_data.py    ← schema and data quality checks on raw CSVs
│   ├── 02_create_curated_data.py  ← transforms raw → curated CSVs
│   ├── 03_load_curated_to_mysql.py ← bulk loads curated CSVs into fab_curated tables
│   └── 04_create_semantic_views.py ← creates the 16 views in fab_semantic
│
└── scripts/
    ├── seed_hub_db.py        ← One-time: creates mcp_servers table + seeds from hub_service/mcp-hub.json
    ├── start_servers.sh      ← Git Bash: starts MySQL (if needed) + all 7 servers incl. hub_service/hub_server.py
    ├── start_servers.ps1     ← PowerShell equivalent of start_servers.sh
    └── health_check.py       ← TCP/HTTP check for all ports (8090, 8001-8003, 9100, 9200, 8080)
```

---

*End of Technical Design — FAB MCP Hub Simple v2.0*
