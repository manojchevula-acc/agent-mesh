# FAB MCP Hub — Solution Architecture

> **Companion documents:**
> - `TECHNICAL_DESIGN.md` — component overview and step-by-step data flows
> - `AUTH.md` — JWT auth deep-dive with standard JWT-for-MCP pattern mapping
> - `RUNBOOK.md` — operational playbook (start/stop/debug)
> - `README.md` — quickstart guide

---

## 1. System Map

```
  ┌────────────────────────────────────────────────────────────────────┐
  │                          BROWSER                                   │
  │   Chat UI (SPA)                  Admin UI (SPA)                   │
  └────────────┬────────────────────────────┬───────────────────────────┘
               │  SSE (GET /sse)            │  HTTP REST
               │  POST /messages            │  (hub JWT required)
               ▼                            ▼
  ┌─────────────────────────┐   ┌────────────────────────────────────────┐
  │  chat_service/          │   │  hub_service/hub_server.py  :8090      │
  │  chat_server.py  :8080  │   │                                        │
  │                         │   │  GET  /.well-known/jwks.json  (public) │
  │  PBKDF2-SHA256 auth     │   │  GET  /health                (public)  │
  │  Rate-limited login     │   │  GET  /servers               (auth)    │
  │  Background task SSE    │   │  POST /discover              (auth)    │
  │  Conversation history   │   │  CRUD /api/hub/*             (admin)   │
  └──────────┬──────────────┘   └──────────────┬─────────────────────────┘
             │                                  │
             │  run_agent(query)                │  MySQL queries
             ▼                                  ▼
  ┌──────────────────────────┐   ┌──────────────────────────────────────┐
  │  agent.py  Orchestrator  │   │  MySQL :3306  fab_semantic           │
  │                          │   │                                      │
  │  POST /discover          │   │  mcp_servers  — server registry      │
  │  mcp_session()           │   │  hub_events   — structured event log │
  │  LangGraph ReAct loop    │   │  conversations, messages             │
  │  astream_events(v2)      │   │  users (chat), sessions              │
  └──────────┬───────────────┘   └──────────────────────────────────────┘
             │  MCP protocol
             │  + RS256 JWT Bearer token (aud = server_id)
             ▼
  ┌──────────────────────────────────────────────────────────────────────┐
  │                    MCP Servers                                       │
  │                                                                      │
  │  Demo servers (streamable-HTTP, FastMCP JWTVerifier):                │
  │    weather_server.py  :8001   calc_server.py  :8002                  │
  │    data_server.py     :8003                                          │
  │                                                                      │
  │  FAB data layer (streamable-HTTP, FastMCP JWTVerifier):              │
  │    customer_server.py :9100   pricing_server.py :9200                │
  │    ── both connect to MySQL fab_semantic via MYSQL_USER/PASSWORD ─── │
  └──────────────────────────────────────────────────────────────────────┘
```

**Local LLM:** Ollama llama3.2:3b at `http://localhost:11434/v1` (OpenAI-compatible)
Used for: hub routing decisions (hub's ReAct agent) + agent's tool-selection ReAct loop.

---

## 2. Authentication Architecture

### 2.1 RS256 Key Pair

The hub generates a 2048-bit RSA key pair on first startup:

```
hub_service/
  private.pem   — hub uses this to sign JWTs   (never leaves the hub process)
  public.pem    — hub publishes this at JWKS    (read-only, safe to share)
```

MCP servers never see `private.pem`. They fetch the hub's public key at startup via:

```
GET http://localhost:8090/.well-known/jwks.json
→ {"keys": [{"kty":"RSA","use":"sig","alg":"RS256","n":"...","e":"AQAB"}]}
```

### 2.2 Token Lifecycle

```
 USER LOGIN          HUB                    AGENT              MCP SERVER
 ──────────          ───                    ─────              ──────────
 POST /login    →    PBKDF2 verify
                     mint hub JWT ────────► stored in
                     (8h, aud=hub)          chat_server
                                            session
                          ▼
 user sends query    POST /discover
 (hub JWT in header) ────────────────────►  for each matched server:
                                            mint per-server JWT
                                            (1h, aud=server_id,
                                             sub=user, roles=forwarded)
                                            ◄─── return [{server, token}, ...]

                               ┌── INBOUND VERIFICATION (agent.py) ──────────────┐
                               │  _verify_hub_token(token, audience=server_id)   │
                               │  • RS256 sig via /.well-known/jwks.json          │
                               │  • checks iss, aud=server_id, exp               │
                               │  Hard fail → server skipped (not connected)      │
                               │  Soft fail → warning; MCP server validates again │
                               └─────────────────────────────────────────────────┘

                                            mcp_session(server):
                                              token = server["server_token"]
                                              Authorization: Bearer <token>
                                              ──────────────────────────────►
                                                             JWTVerifier (primary):
                                                               RS256 via JWKS ✓
                                                               verify aud ✓
                                                               verify iss ✓
                                                               verify exp ✓
                                                               → 401 if ANY fail
                                                             ClaimsExtractorMiddleware:
                                                               unverified decode ✓
                                                               (JWTVerifier already
                                                                validated the token)
                                                               → _request_claims ContextVar
                                                             Tool function:
                                                               require_role("agent") ✓
                                                               audit_log(tool, args) ✓
                                                               ── execute ──
```

**Agent standalone path** also has INBOUND VERIFICATION for the hub login JWT:

```
Agent → POST /auth/login → Hub returns access_token
                                │
                                ▼  [INBOUND VERIFICATION — Step 2a]
                           _verify_hub_token(token)   (agent.py)
                           • RS256 sig via /.well-known/jwks.json
                           • checks iss=fab-mcp-hub, exp
                           • Hard fail → login rejected; token not cached
                           • Soft fail → warning; token cached
```

### 2.3 Per-Server Audience Scoping

Each `/discover` call mints a **separate** JWT per matched server with `aud = server_id`.

- A token for `fab-customer-server` is rejected by `fab-pricing-server` (wrong `aud`).
- Prevents cross-server token replay if a token is intercepted.
- Token expiry is 1 hour (intentionally shorter than the 8-hour user session) so leaked tokens are short-lived.

### 2.4 Credential Isolation

| Layer | Credential | Used By | Never Forwarded To |
|-------|-----------|---------|-------------------|
| Browser → Chat | Hub JWT (8h) | chat_server login check | Anything beyond hub |
| Agent → Hub | Hub JWT (8h) | `/discover` auth | MCP servers |
| Agent → MCP | Per-server JWT (1h) | MCP JWTVerifier | MySQL, external APIs |
| MCP → MySQL | `MYSQL_USER` / `MYSQL_PASSWORD` | SQLAlchemy engine | Agent, hub, browser |
| MCP → External APIs | `MCP_TOOL_KEY` | `service_auth_headers()` | Agent, hub, browser |

No credential crosses a layer boundary. The agent JWT is consumed at the MCP boundary and discarded.

---

## 3. Hub Routing Architecture

```
POST /discover  (with hub JWT)
       │
       ▼
 load_hub() ──► MySQL mcp_servers ──► 60s in-process cache (_hub_cache)
       │
       ▼
 LLM enabled?
  ├─ YES → _agent_route()
  │         creates fresh LangGraph ReAct agent (new instance per request
  │         to prevent concurrent request state collision)
  │         injects: server list, user query
  │         tool: pick_server(server_id, reason)
  │         LLM picks the best-matching server → returns server config
  │
  └─ NO  → return first registered server (deterministic fallback)
       │
       ▼
 for each matched server:
   mint RS256 JWT (aud=server_id, sub=user, roles=user_roles, exp=1h)
   append {server_config, server_token} to response
```

**Why a new routing agent per request:** LangGraph's ReAct agent holds internal message state. Two concurrent requests sharing one agent instance would interleave their messages and produce incorrect routing. A new instance costs ~1ms and is always safe.

**Why 60-second cache:** MySQL round-trips add 2–5ms latency per `/discover` call. With a 60s cache, the first call in each window pays the DB cost; subsequent calls within that window return instantly. `/api/hub/refresh` busts the cache immediately for admin changes.

---

## 4. Observability Architecture

Every `log_event()` call fans out to four sinks simultaneously:

```
log_event(type, **data)
       │
       ├──► in-memory deque  (maxlen=500)   — GET /api/logs fast read
       │
       ├──► stdout (print)                  — docker logs / console
       │
       ├──► logs/hub.log  (JSONL, line-buffered)  — persistent file log
       │        opened once at module import;
       │        line-buffered = each event flushed immediately
       │
       └──► MySQL hub_events table          — queryable history
                (disabled permanently if MySQL is unreachable;
                 restart required to re-enable)
```

**Event types logged:**

| Type | When | Key fields |
|------|------|-----------|
| `auth` | Every token check | sub, roles, path, valid |
| `request` | Every HTTP request | method, path, status, latency_ms |
| `routing` | Server selection | method, server_id, reason, intent |
| `tool_audit` | Before each tool call | tool, service, sub, roles, args_keys |
| `error` | Runtime exceptions | message, traceback |

**GET /api/logs** reads from MySQL when available (indexed `ORDER BY ts DESC LIMIT n`, then `reversed()` for chronological output). Falls back to in-memory deque when MySQL is unavailable.

---

## 5. Chat Session Architecture

```
Browser                chat_server.py                    MySQL
───────                ──────────────                    ─────
POST /login  ────────► PBKDF2 verify
                       mint hub JWT (8h)
                       ◄──── set cookie ──

GET /  ◄──────────── serve SPA (inline HTML/JS)

EventSource           GET /sse  ────────────────────────────────────────────
/sse opens  ◄────────    create conversation_id in MySQL
persistent             create asyncio.Queue
SSE connection         yield events from Queue
                       ─────────────────────────────────────────────────────

POST /messages ───────► create Task: run_agent(query, on_event)
                          │
                          │  on_event callback → put into Queue → SSE stream
                          │
                          │  asyncio.Task persists after browser disconnect
                          │  final answer saved to MySQL conversations
                          │  GET /poll re-attaches to retrieve missed events
```

**Background task lifecycle:** The asyncio.Task is created before the SSE generator starts. If the browser disconnects mid-stream, the Task continues running and saves the final answer to MySQL. The browser can call `GET /poll?session_id=...` to retrieve the answer it missed.

**Memory leak warning:** `_bg_tasks` dict has no TTL. Tasks accumulate for the process lifetime. For long-running deployments, implement periodic cleanup or use an external task queue.

---

## 6. MCP Transport Support

The agent's `mcp_session()` context manager abstracts two transports:

| Transport | Protocol | Used by | Session state |
|-----------|----------|---------|--------------|
| Streamable HTTP | POST /mcp (stateful) | All 5 servers — demo (calc, weather, data) + FAB (customer, pricing) | `Mcp-Session-Id` header |

The Bearer JWT is sent on **every JSON-RPC call** (initialize, tools/list, tools/call), not just the initial handshake. FastMCP validates it on each request independently.

> **Note:** SSE transport (`/sse`) was removed in v2.1 — all servers now use streamable-HTTP at `/mcp/`.

Streamable-HTTP requires an `initialize` handshake first; the session ID returned must be included in all subsequent calls. The `Accept: application/json, text/event-stream` header is required — FastMCP returns 406 if either MIME type is absent.

---

## 7. Data Layer Architecture

The FAB MCP servers expose SQL-backed business data as MCP tools:

```
agent.py
  └── mcp_session(fab-customer-server)
        └── tools/call  customer_360(customer_id="CUST007")
              └── mcp_server/tools.py
                    └── mcp_server/db.py  (SQLAlchemy)
                          └── MySQL fab_semantic
                                ├── 14 base tables   (raw customer, product, price data)
                                └── 16 semantic views (pre-joined, business-readable)
```

MCP tools query the **semantic views** (not raw tables). This isolates the tool API from schema changes — if a base table is restructured, only the view needs updating; the tool signature stays the same.

---

## 8. Key Design Decisions

### RSA over HMAC for JWT signing

**Decision:** RS256 (asymmetric) rather than HS256 (shared secret).

**Why:** With HMAC, every MCP server needs a copy of the secret key to verify tokens — meaning a compromised MCP server leaks the signing key for all other servers. With RSA, MCP servers only need the public key (fetched from JWKS); the private key stays in the hub process only.

### Per-server tokens (not one shared token)

**Decision:** `/discover` mints a separate JWT per matched MCP server.

**Why:** A single token that works on all servers creates a large blast radius if intercepted. Audience-scoped tokens contain the damage to one server.

### 60-second hub cache over per-request MySQL

**Decision:** Cache the server registry for 60 seconds; invalidate via API.

**Why:** `/discover` is called on every user query. A MySQL query per call adds latency and load. 60 seconds is short enough that admin changes appear quickly; `/api/hub/refresh` gives immediate invalidation when needed.

### Permanent MySQL skip on first failure (observability)

**Decision:** `_db_failed = True` permanently disables MySQL logging after the first failure.

**Why:** If MySQL becomes slow, retrying on every `log_event()` call would add latency to every HTTP request. Permanent skip ensures logging never blocks the request path. In-memory + file sinks remain active, so observability is not lost.

### asyncio.Task for background agent execution

**Decision:** Create the agent Task before the SSE generator opens.

**Why:** If the Task were created inside the generator, a browser disconnect would cancel both the generator and the Task, losing the in-progress agent work. Decoupling Task lifetime from SSE lifetime means the answer is always saved to MySQL.

---

## 9. Security Properties

| Property | Implementation |
|----------|---------------|
| Token forgery prevention | RS256 — only hub's private key can sign; all verifiers check against JWKS public key |
| Cross-server token replay | Per-server `aud` claim — token rejected by any server other than its intended audience; verified at agent AND MCP layers |
| Token expiry | Hub tokens: 8h; per-server tokens: 1h |
| Credential isolation | Each layer uses its own credential; none are forwarded across boundaries |
| Brute-force resistance | Login rate-limited per username (10 attempts / 15 min); PBKDF2-SHA256 (200,000 iterations) |
| Agent-side inbound verification | `_verify_hub_token()` in `agent.py` uses FastMCP `JWTVerifier` (via `_get_hub_jwt_verifier()`) to verify hub login JWT (Step 2a) and each per-server JWT (Step 2b) — same pattern as MCP servers; catches forged tokens before MCP connection |
| MCP token validation | FastMCP `JWTVerifier` is the sole cryptographic gatekeeper — RS256 via hub JWKS; rejects 401 on any failure |
| MCP claims extraction | `ClaimsExtractorMiddleware` performs unverified decode (no second JWKS call) to populate `_request_claims` ContextVar for RBAC |
| Dev-mode flag | `MCP_AUTH_ENABLED=false` completely disables MCP auth — only for local dev |
| Dev fallback risk | `_verify_jwt()` grants admin when no token + no `MCP_SERVER_ID` — never deploy without `MCP_SERVER_ID` set |
| .env isolation | `datalayer-as-service/.env` contains real secrets; `.gitignore` must exclude it |

---

## 10. Key Files Reference

| File | Role |
|------|------|
| `hub_service/hub_server.py` | FastAPI hub: server registry, routing, admin SPA, JWKS endpoint; async `_require_auth` routes by alg (RS256 → `JWTVerifier`; HS256 → `verify_token`) |
| `hub_service/auth.py` | Hub auth helpers: `build_hub_jwt_verifier()`, `generate_server_token()`, `verify_token()`, JWKS construction |
| `hub_service/db.py` | SQLAlchemy engine factory; reads credentials from `datalayer-as-service/.env` |
| `hub_service/observability.py` | 4-way event fan-out (memory + stdout + file + MySQL) |
| `chat_service/chat_server.py` | Chat UI SPA server; user auth; SSE streaming; background tasks |
| `agent.py` | LangGraph ReAct orchestrator; hub discovery; MCP session; tool loading |
| `datalayer-as-service/mcp_server/auth.py` | JWT verification; RBAC (`require_role`); audit logging |
| `datalayer-as-service/mcp_server/server.py` | FastMCP server setup; JWTVerifier wiring |
| `datalayer-as-service/mcp_server/tools.py` | MCP tool implementations (customer, pricing) |
| `datalayer-as-service/mcp_server/db.py` | MySQL connection for MCP data tools |
| `scripts/seed_hub_db.py` | Idempotent seeder: creates `mcp_servers` table; syncs from `mcp-hub.json` |
| `hub_service/mcp-hub.json` | Source-of-truth server registry (JSON); seeded into MySQL |
| `hub_service/private.pem` | RSA private key (hub only; never shared) |
| `hub_service/public.pem` | RSA public key (published at JWKS) |
| `datalayer-as-service/.env` | MySQL credentials + MCP config (never commit) |
| `logs/hub.log` | JSONL structured event log |
