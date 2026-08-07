# FAB MCP Hub — Runbook

This is the short path to get the stack running locally.

## 1. Prerequisites

- Python 3.11+ with the project venv
- Ollama running locally
- MySQL running locally

## 2. Setup

```powershell
copy .env.example .env
python scripts/seed_hub_db.py
```

## 3. Start everything

```powershell
.\scripts\start_servers.ps1
```

Or on Git Bash:

```bash
bash scripts/start_servers.sh
```

## 4. Verify

- Open http://localhost:8080
- Check the hub: http://localhost:8090/health
- Check the JWKS endpoint: http://localhost:8090/.well-known/jwks.json
- Run:

```powershell
python scripts/health_check.py
```

## 5. Generate a token

```powershell
python hub_service/auth.py --sub agent --roles agent --hours 24 --audience fab-customer-server --server-id fab-customer-server
```

Use the returned value as the Bearer token for the hub or MCP servers.

## Troubleshooting

- MySQL not starting: start it manually and confirm port 3306 is open.
- Hub not responding: check the console output and retry the health endpoint.
- Port already in use: stop the stale process and restart the scripts.


| Tab | Shows |
|---|---|
| **Auth** | Hub auth events (sub, roles, token_type, pass/fail) |
| **Routing** | Hub routing decisions (server selected, method, reason) |
| **Requests** | All hub HTTP requests (method, path, status, latency). **`request_detail` entries are expandable** — click **detail** to see full request body and response body for `/discover` and key endpoints |
| **Stats** | Auth pass rate donut, event type breakdown, identity table |

---

## Step 5 — Admin UI

```
http://localhost:8090/admin
```

### Login

| Username | Password | Source |
|---|---|---|
| admin | admin | `HUB_ADMIN_USERNAME` / `HUB_ADMIN_PASSWORD` in `.env` |

The admin generates an 8-hour JWT. The token is stored in `sessionStorage` and sent as `Authorization: Bearer <token>` on every API call.

> **Dev mode without JWT_SECRET:** login still works — a readable dev token is returned. The token hint in the Security tab shows `dev-open`.

### Admin tabs

| Tab | Purpose |
|---|---|
| **Servers** | List all registered MCP servers with full API key + Copy button. Test / Tools / Key / History / Details / Edit / Delete per server |
| **Logs** | Live event log. Filter by type: auth, request, request_detail, routing, admin, error. `request_detail` rows show full `/discover` request/response bodies |
| **Auth & Tokens** | Current auth config, JWT generator, MCP auth status |

### Servers tab — per-server actions

| Button | What it does |
|--------|-------------|
| **Test** | Sends a live ping using a hub-minted RS256 JWT. Shows response JSON, latency, Bearer JWT used, and a ready-to-run curl command. Headers and body are editable for custom re-runs. Auto-refresh every 5 seconds toggle is available. |
| **Tools** | Lists all tools the server exposes: Tool Name, Description, **Input Schema** (formatted JSON), and **Output Schema** (formatted JSON). |
| **Key** | View, set, or clear the per-server API key stored in MySQL. Generate a hub-signed RS256 JWT directly from the modal. |
| **History** | Recent auth and routing events for this server. |
| **Details** | Transport, endpoint, capability info with ready-made curl examples. |
| **Edit** | Update name, endpoint, transport, or capability. |
| **Delete** | Remove from registry. |

**Key column** — shows the **full API key** for each server with a **Copy** button. Click Copy to copy the key to the clipboard.

### Managing per-server MCP keys (Credentials)

Each MCP server can have its own `api_key` stored in MySQL. When set, the agent uses that key **instead of** the shared `MCP_API_KEY` env var.

1. In Admin UI → Servers tab → click **🔑 Key** next to any server
2. Current key status shows (set/not set, full key, expiry)
3. Paste a new API key or JWT, or click **Generate JWT** for a hub-signed RS256 JWT
4. Optionally set expiry in hours
5. Click **Save Key**

To generate a JWT for a specific server from the CLI:
```bash
python hub_service/auth.py --sub fab-agent --roles agent --hours 8760 --server-id fab-customer-server
# Paste the output into the Key modal
```

To clear a per-server key (revert to `MCP_API_KEY` env var):
- Click **Clear Key (use env fallback)** in the Key modal

**Key resolution order:**
```
1. mcp_servers.api_key column in MySQL  (per-server, set via Admin UI)
2. MCP_API_KEY env var                  (shared fallback)
3. No key                               (dev open mode — MCP server must have no keys configured)
```

> **How Test and Tools probe auth works:** The hub mints a short-lived RS256 JWT (1-hour, `aud=server_id`) for each probe request. This is the same token format the agent uses — FastMCP's JWTVerifier accepts it automatically. If JWT minting fails (e.g., `.keys/` missing), the probe falls back to the stored per-server API key.

---

## Step 6 — External tool credentials

Three tools in the Pricing server call external services with **independent credentials**:

| Tool | Auth pattern | External service | Credential var |
|---|---|---|---|
| `credit_bureau_check` | Bearer JWT | `POST /check` | `CREDIT_BUREAU_VALID_TOKEN` |
| `fx_rate_lookup` | X-API-Key header | `GET /fx/{pair}` | `FX_RATE_API_KEY` |
| `sanctions_screen` | Bearer JWT (admin only) | `POST /sanctions` | `SANCTIONS_VALID_TOKEN` |

Dev tokens are in `datalayer-as-service/tool_credentials.db` (seeded automatically on first MCP server start).

### Start the mock external service

```bash
python datalayer-as-service/mcp_server/external_service.py
# Runs on port 8010
# GET http://localhost:8010/tokens — shows expected dev tokens
```

### Rotate a credential without restart

```bash
python datalayer-as-service/mcp_server/tool_registry.py \
  --rotate credit_bureau_check new-token-value
```

---

## Step 7 — CLI agent usage

```bash
# Weather
python agent.py "What is the current weather in Tokyo?"

# Calculator
python agent.py "Calculate sqrt(225)"
python agent.py "Convert 100 km to miles"

# Customer Intelligence
python agent.py "Show me the 360 profile for CUST001"
python agent.py "Show credit rating events for CUST003"
python agent.py "What are cross-sell opportunities for CUST002?"

# Pricing
python agent.py "What pricing should I recommend for CUST001?"
python agent.py "Which deals are non-compliant?"
python agent.py "Run competitor price analysis for CUST002"

# Multi-server fan-out
python agent.py "Comprehensive analysis of CUST001"

# External tools (requires external_service.py on port 8010)
python agent.py "Check the credit bureau for CUST001"
python agent.py "What is the FX rate for USDAED?"
```

---

## Authentication reference

### Layer 1 — Agent → Hub (`HUB_API_KEY`)

Set in `.env`:
```
AUTH_ENABLED=true
HUB_API_KEY=<token>    # static key or JWT
JWT_SECRET=<secret>    # required to mint JWTs from Admin UI
```

Mint a JWT for `HUB_API_KEY`:
```bash
python hub_service/auth.py --sub fab-agent --roles agent --hours 8760
```

### Layer 2 — Agent → MCP servers (`MCP_API_KEY` or per-server DB key)

Set in `.env`:
```
MCP_AUTH_ENABLED=true
MCP_API_KEY=<token>       # shared fallback for all MCP servers
MCP_JWT_SECRET=<secret>   # required to validate JWTs on MCP servers
```

Mint a JWT for `MCP_API_KEY`:
```bash
python datalayer-as-service/mcp_server/auth.py --sub fab-agent --roles agent --hours 8760
```

Per-server keys override `MCP_API_KEY` — set via Admin UI or API:
```bash
curl -X PUT http://localhost:8090/api/mcp-credentials/fab-pricing-server \
  -H "Authorization: Bearer <admin-jwt>" \
  -H "Content-Type: application/json" \
  -d '{"api_key": "<token>", "expires_hours": 8760}'
```

### Layer 3 — MCP → MySQL (service credentials)

Set in `datalayer-as-service/.env`:
```
MYSQL_USER=test_user
MYSQL_PASSWORD=Welcome@12345
MYSQL_DATABASE=fab_semantic
```

The agent JWT **never** reaches MySQL.

### MySQL tables (all in `fab_semantic`)

| Table | Owner | Contains |
|---|---|---|
| `mcp_servers` | Hub | Server registry — id, endpoint, transport, capability, `api_key` |
| `mcp_server_changelog` | Hub | Audit trail of server create/update/delete/key-rotate |
| `hub_events` | Hub observability | All auth, request, routing, admin events (shown in Admin UI Logs tab) |
| `chat_sessions` | Chat server | Session id, username, status, start/end time |
| `chat_messages` | Chat server | Per-message query + final answer per session |
| `chat_traces` | Chat server | Per-event trace entries (shown in Timeline/Security/Perf tabs) |
| `chat_users` | Chat server | Usernames, hashed passwords, roles |

> All storage is MySQL-only — no SQLite, no in-memory fallback for users.

### Layer 4 — MCP → External services (tool registry)

Stored in `datalayer-as-service/tool_credentials.db` (SQLite). Managed via:
```bash
python datalayer-as-service/mcp_server/tool_registry.py --list
python datalayer-as-service/mcp_server/tool_registry.py --rotate <tool_name> <new_credential>
```

### Dev mode (no keys configured)

When `AUTH_ENABLED=true` but no `HUB_API_KEY` or `JWT_SECRET` is set:
- Hub auth is open — all requests granted `admin` role
- Admin UI login still works — returns a dev token that the system accepts

When `MCP_AUTH_ENABLED=true` but no `MCP_API_KEY` or `MCP_JWT_SECRET` is set:
- MCP auth is open — all requests granted `admin` role
- `_MCP_DEV_MODE_ACTIVE=True`

---

## RBAC roles

| Role | Hub endpoints | MCP tools |
|---|---|---|
| `admin` | All including `/api/logs` | All tools including `policy_exception`, `non_compliant_deals`, `sanctions_screen` |
| `agent` | `/discover`, `/servers`, `/health` | Most tools (not admin-only ones) |
| `readonly` | `/servers`, `/health` | Read-only subset (future) |

---

## Troubleshooting

### Admin UI login fails

**Symptom:** Login form shows "Failed" or no response.

**Cause 1 — JS syntax error in the page** (usually after a code change):
- Open browser DevTools → Console tab
- If you see `SyntaxError: Unexpected token` the HTML has a JS error
- Restart the hub server with the latest code

**Cause 2 — Wrong password:**
- Default: `admin` / `admin`
- Check `.env` for `HUB_ADMIN_USERNAME` / `HUB_ADMIN_PASSWORD`

**Cause 3 — JWT_SECRET not set but auth is enabled:**
- The admin login generates a dev token automatically when `JWT_SECRET` is empty
- If this still fails, check that `hub_server.py` is running the latest code

**Alternative: use the "Sign in with token" tab:**
```bash
# Generate a token from CLI
python hub_service/auth.py --sub admin --roles admin --hours 24
# Paste it into the "Token" field on the login page
```

---

### Chat reaches hub but not MCP server

**Symptom:** Timeline shows `hub_loaded`, `routing`, `mcp_connecting`, then an **error** card.

**Cause 1 — MCP server not running:**
```bash
python scripts/health_check.py
# Look for FAIL next to the server endpoint
```
Start the missing server:
```bash
cd datalayer-as-service
MCP_TRANSPORT=http MCP_HOST=127.0.0.1 MCP_PORT=9100 python -m mcp_server.customer_server &
MCP_TRANSPORT=http MCP_HOST=127.0.0.1 MCP_PORT=9200 python -m mcp_server.server &
```

**Cause 2 — Auth mismatch (401 error):**
The Security tab will show a red `REJECTED-401` auth hop. This means the agent's key doesn't match what the MCP server expects.

Check the key source in the error:
- `key_source=per-server-db` → the per-server key in MySQL doesn't match the MCP server's `MCP_API_KEY`. **The hub auto-aligns these on restart** (resets any `mcp-` prefixed auto-generated keys to `MCP_API_KEY`). Just restart the hub.
- `key_source=env-MCP_API_KEY` → the `MCP_API_KEY` in `.env` is wrong or not loaded

To force immediate realignment without restart:
```bash
# Manually align MySQL per-server keys with MCP_API_KEY:
.venv/Scripts/python.exe -c "
import pymysql, os; from dotenv import load_dotenv; load_dotenv('.env')
key = os.environ['MCP_API_KEY']
conn = pymysql.connect(host='127.0.0.1', user='test_user', password='Welcome@12345', database='fab_semantic')
c = conn.cursor(); c.execute(\"UPDATE mcp_servers SET api_key=%s WHERE api_key LIKE 'mcp-%%'\", (key,))
print('Updated:', c.rowcount, 'rows'); conn.commit(); conn.close()
"
# Then refresh hub cache:
curl -X POST http://localhost:8090/api/hub/refresh -H "Authorization: Bearer <admin-jwt>"
```

Verify the key the MCP server expects:
```bash
# The MCP server logs auth decisions — check terminal output for:
# {"type": "auth", "valid": false, "agent_sub": "unknown", ...}
```

**Cause 3 — Server.py fails to start:**
Check the MCP server terminal for import errors. If `tool_registry.py` is missing:
```bash
python datalayer-as-service/mcp_server/tool_registry.py --seed
```

---

### Per-server MCP key shows "env" in Admin UI Key column

**Symptom:** Key column shows `env` for a server instead of the full key value.

**Meaning:** No per-server key is stored in MySQL for that server — it uses the shared `MCP_API_KEY` env var.

**To set a per-server key:**
1. Click **🔑 Key** next to the server
2. Paste a key or click **Generate JWT**
3. Click **Save Key** — the Key column will then show the full key with a Copy button

**If `api_key` column is missing from MySQL (first run):**
```bash
python scripts/seed_hub_db.py
# Should print "adding column: api_key" and "adding column: api_key_expires"
```

After running, the Key column and 🔑 Key button work for all servers.

---

### External tool returns "Tool registry error"

**Symptom:** `credit_bureau_check` or `fx_rate_lookup` returns `"Tool registry error: tool 'X' not registered"`.

**Fix:**
```bash
python datalayer-as-service/mcp_server/tool_registry.py --seed
```

Or set `AUTO_SEED_CREDENTIALS=true` in `.env` (default is `true`).

---

### External tool returns 401

**Symptom:** `"External service auth failed (401) — credential may be wrong or expired."`

**Check that external_service.py is running and tokens match:**
```bash
curl http://localhost:8010/tokens
# Shows expected tokens for each service
```

**Rotate the credential:**
```bash
python datalayer-as-service/mcp_server/tool_registry.py \
  --rotate credit_bureau_check credit-bureau-dev-token
```

---

### Hub routes to wrong server

```bash
# More specific query language helps
# Instead of: "CUST001"
# Use: "Show me the 360 profile for CUST001"
# Instead of: "pricing"
# Use: "What pricing recommendation for CUST001?"

# Use a larger model for better routing
export OLLAMA_MODEL=qwen2.5:7b   # Git Bash
$env:OLLAMA_MODEL="qwen2.5:7b"   # PowerShell
```

---

### Port already in use

```bash
# Find and kill process on port 9100
pid=$(netstat -ano 2>/dev/null | awk '/0.0.0.0:9100 /{print $NF}' | head -1)
[ -n "$pid" ] && taskkill //PID "$pid" //F
```

Kill all hub ports and restart:
```bash
for port in 8001 8002 8003 8010 8080 8090 9100 9200; do
  pid=$(netstat -ano 2>/dev/null | awk "/0.0.0.0:$port / {print \$NF}" | head -1)
  [ -n "$pid" ] && taskkill //PID "$pid" //F &>/dev/null || true
done
bash scripts/start_servers.sh
```

---

### MySQL not running after reboot

```
Error: Can't connect to MySQL server on '127.0.0.1:3306'
```

```bash
"/c/Program Files/MySQL/MySQL Server 8.4/bin/mysqld.exe" \
  --defaults-file="C:/MySQL/my.ini" &
```

---

### Slow responses

Expected for llama3.2:3b on CPU: 2–10 seconds per LLM call.  
Multi-server queries: 20–40 seconds.

Enable GPU acceleration by installing Ollama with CUDA support and confirming:
```bash
ollama ps   # shows CPU vs GPU
```

---

## Environment variables reference

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama endpoint |
| `OLLAMA_MODEL` | `llama3.2:3b` | Model for routing and tool loops |
| `HUB_HOST` | `0.0.0.0` | Hub server bind address |
| `HUB_PORT` | `8090` | Hub server port |
| `HUB_LLM_ENABLED` | `true` | `false` = first-match routing (no LLM) |
| `HUB_SERVER_URL` | `http://localhost:8090` | Hub URL used by agent |
| `HUB_ADMIN_USERNAME` | `admin` | Admin UI username |
| `HUB_ADMIN_PASSWORD` | `admin` | Admin UI password |
| `AUTH_ENABLED` | `true` | `false` = bypass all hub auth |
| `AUTH_PROVIDER` | `local` | `local` or `azure` |
| `HUB_API_KEY` | *(empty)* | Static bearer token or JWT for hub |
| `JWT_SECRET` | *(empty)* | HS256 signing secret (hub + chat share this) |
| `CHAT_HOST` | `0.0.0.0` | Chat server bind address |
| `CHAT_PORT` | `8080` | Chat server port |
| `CHAT_USERS` | *(hardcoded)* | Override users: `name:pass:role\|...` |
| `MCP_AUTH_ENABLED` | `true` | `false` = bypass all MCP server auth |
| `MCP_AUTH_PROVIDER` | `local` | `local` or `azure` |
| `MCP_API_KEY` | *(empty)* | Shared bearer token sent to all MCP servers |
| `MCP_JWT_SECRET` | *(empty)* | HS256 signing secret for MCP server JWT validation |
| `AUTO_SEED_CREDENTIALS` | `true` | Auto-seed `tool_credentials.db` on first MCP server start |
| `MYSQL_USER` | *(in datalayer/.env)* | MySQL username |
| `MYSQL_PASSWORD` | *(in datalayer/.env)* | MySQL password |
| `MYSQL_DATABASE` | `fab_semantic` | MySQL database |

---

## File map

```
fab-mcp-hub-simple/
│
├── .env                          ← Root env: all server keys, JWT secrets, ports, MySQL creds
├── agent.py                      ← Orchestrator: hub /discover + MCP client + ReAct loop
│                                    Per-server key from mcp_servers.api_key (or MCP_API_KEY fallback)
├── test_agent.py                 ← Integration tests
│
├── hub_service/
│   ├── hub_server.py             ← FastAPI hub (port 8090)
│   │                                /discover, /admin, /api/mcp-credentials CRUD
│   │                                startup: auto-aligns mcp_servers.api_key with MCP_API_KEY
│   │                                hub_events persisted to MySQL hub_events table
│   ├── auth.py                   ← Hub auth: AUTH_ENABLED, local JWT, Azure AD stub
│   ├── db.py                     ← SQLAlchemy engine (reads datalayer-as-service/.env for MySQL creds)
│   ├── observability.py          ← Event log: ring buffer + MySQL hub_events persistence
│   └── mcp-hub.json              ← Seed source for mcp_servers table
│
├── chat_service/
│   └── chat_server.py            ← Chat UI (port 8080)
│                                    All storage in MySQL: chat_sessions, chat_messages,
│                                    chat_traces, chat_users — no SQLite, no in-memory fallback
│
├── datalayer-as-service/
│   ├── .env                      ← MySQL credentials (MCP_SERVER_ID empty = use MCP_API_KEY env)
│   ├── tool_credentials.db       ← SQLite: per-tool external service credentials (auto-created)
│   └── mcp_server/
│       ├── auth.py               ← MCP auth: BearerAuthMiddleware, per-server DB key support
│       │                            MCP_SERVER_ID set → loads key from MySQL mcp_servers.api_key
│       │                            Fallback → MCP_API_KEY env var
│       ├── tool_registry.py      ← SQLite credential store for external tools
│       ├── external_service.py   ← Mock external services (port 8010): credit, FX, sanctions
│       ├── server.py             ← FAB Pricing MCP server (port 9200)
│       ├── customer_server.py    ← FAB Customer Intelligence (port 9100)
│       ├── pricing_server.py     ← Alias entry point for server.py
│       ├── weather_server.py     ← Demo weather (port 8001)
│       ├── calc_server.py        ← Demo calculator (port 8002)
│       ├── data_server.py        ← Demo reference data (port 8003)
│       ├── tools.py              ← MySQL query functions
│       └── db.py                 ← MySQL pool
│
└── scripts/
    ├── seed_hub_db.py            ← Create + seed mcp_servers table (incl. api_key column)
    ├── start_servers.sh          ← Git Bash: start all servers (sets MCP_SERVER_ID per server)
    ├── start_servers.ps1         ← PowerShell: start all servers (sets MCP_SERVER_ID per server)
    └── health_check.py           ← Verify all endpoints
```

---

## Trace event reference

| Event | Timeline icon | Meaning |
|---|---|---|
| `auth_hop` | 🔐 | Auth token used at one hop (browser→chat, agent→hub, agent→mcp). Expand to see JWT claims, key_source |
| `hub_loaded` | 📋 | Hub name + server list loaded |
| `routing` | 🔶 | Which server(s) selected, method (llm/fast_path), reason |
| `mcp_connecting` | 🔌 | Connecting to MCP server (server_id, endpoint, transport) |
| `mcp_connected` | 🔧 | MCP session open, tool list discovered |
| `tool_call` | ⚙️ | Tool name + args. **Expand** → formatted request JSON |
| `tool_result` | 📤 | Tool result. **Expand** → pretty-printed response JSON |
| `external_tool_call` | 🔗 | MCP server called an external service. Shows auth pattern + key source. Also visible in Security tab |
| `final_answer` | ✅ | Agent's answer to the query |
| `error` | ❌ | Failure with human-readable message (auth mismatch, unreachable, etc.) |

*Last updated: 2026-08-05*
