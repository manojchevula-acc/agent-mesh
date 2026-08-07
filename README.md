# FAB MCP Hub

A local MCP hub for demos and development. It runs the hub, chat UI, and MCP servers on localhost with Ollama and MySQL.

The hub acts as the central auth service — it signs RS256 JWTs and exposes a JWKS endpoint. MCP servers validate those JWTs against the hub's public key. The Admin UI lets you test, inspect, and manage all registered servers without touching the terminal.

---

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Python 3.11+ | 3.13 recommended |
| MySQL 8.x | Must be running before starting any server |
| Ollama | `ollama serve` — any local model, default `llama3.2:3b` |
| Python venv | Create once; activate before every session |

**Create the virtual environment (first time only):**
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

---

## Step 1 — Configure environment

```powershell
copy .env.example .env
```

Open `.env` and set at minimum:

| Variable | What to set |
|----------|-------------|
| `JWT_SECRET` | Any random string — hub and chat share this |
| `HUB_ADMIN_PASSWORD` | Admin UI password (default `admin`) |
| `HUB_AGENT_USERNAME` / `HUB_AGENT_PASSWORD` | Agent login credentials |
| `MYSQL_USER` / `MYSQL_PASSWORD` | Set in `datalayer-as-service/.env` |

> **Security:** `.env` contains real credentials. It is already in `.gitignore` — never remove it from there.

---

## Step 2 — Start MySQL

MySQL does not start automatically on Windows. Run once after every reboot:

```powershell
& "C:\Program Files\MySQL\MySQL Server 8.4\bin\mysqld.exe" --defaults-file="C:\MySQL\my.ini"
```

Verify it is running:
```powershell
& "C:\Program Files\MySQL\MySQL Server 8.4\bin\mysql.exe" -u test_user -pWelcome@12345 -h 127.0.0.1 fab_semantic -e "SHOW TABLES;"
```

---

## Step 3 — Seed the database (first time only)

```powershell
python scripts/seed_hub_db.py
```

Creates the `mcp_servers` table and inserts 5 registered servers from `hub_service/mcp-hub.json`. Safe to re-run — it is idempotent.

---

## Step 4 — Start all servers

**PowerShell:**
```powershell
.\scripts\start_servers.ps1
```

**Git Bash:**
```bash
bash scripts/start_servers.sh
```

This starts 7 services. Each gets its own window:

| Service | Port | Transport | Notes |
|---------|------|-----------|-------|
| Hub server | 8090 | HTTP | JWT auth, /discover, Admin UI |
| Chat UI | 8080 | HTTP | Browser chat interface |
| Customer MCP | 9100 | streamable-HTTP | FAB Customer Intelligence (MySQL-backed) |
| Pricing MCP | 9200 | streamable-HTTP | FAB Pricing Engine (MySQL-backed) |
| Weather MCP | 8001 | SSE | Demo tool |
| Calculator MCP | 8002 | SSE | Demo tool |
| Data MCP | 8003 | SSE | Demo reference data |

---

## Step 5 — Verify everything is up

```powershell
python scripts/health_check.py
```

Or check individually:
- Hub health: http://localhost:8090/health
- JWKS endpoint: http://localhost:8090/.well-known/jwks.json
- Chat UI: http://localhost:8080
- Admin UI: http://localhost:8090/admin

Check which ports are listening:
```powershell
netstat -ano | findstr "8090 8001 8002 8003 9100 9200 8080"
```

---

## Step 6 — Admin UI

Open **http://localhost:8090/admin** and log in:
- Username: `admin` (or your `HUB_ADMIN_USERNAME`)
- Password: `admin` (or your `HUB_ADMIN_PASSWORD`)

### Servers tab

Lists all registered MCP servers. For each server you can:

| Button | What it does |
|--------|-------------|
| **Test** | Live ping — shows response, latency, the Bearer JWT used, and a copy-able curl command. Headers and body are editable for custom requests. Auto-refresh every 5 seconds toggle available. |
| **Tools** | Lists all tools the server exposes with **Tool Name**, **Description**, **Input Schema** (JSON), and **Output Schema** (JSON). |
| **Key** | View, set, or clear the per-server API key stored in MySQL. Generate a hub-signed RS256 JWT directly from the modal. |
| **History** | Recent auth and routing events for this server. |
| **Details** | Transport, endpoint, and capability info with ready-made curl examples. |
| **Edit** | Update name, endpoint, transport, or capability. |
| **Delete** | Remove from registry. |

**Key column** — shows the full API key for each server with a **Copy** button. Click Copy to copy the key to the clipboard.

### Add a server

1. Click **+ Add Server** (top-right of Servers tab)
2. Fill in ID, name, endpoint, transport, and capability
3. Click **Save** — the hub picks it up immediately

### Set a per-server key

1. Click **🔑 Key** next to any server
2. Paste a new key or click **Generate JWT** for a hub-signed RS256 JWT
3. Set optional expiry in hours
4. Click **Save Key**

To revert to the shared `MCP_API_KEY` env var, click **Clear Key (use env fallback)**.

### Rotate all server keys

Click **Rotate all keys** in the top toolbar. After rotating, restart all MCP servers so they reload their new key from MySQL.

### Refresh server cache

Click **Refresh cache** to force the hub to reload from MySQL immediately (default TTL is 60 seconds).

---

## Managing servers from the terminal

**Add or update a server in the registry:**
```powershell
# Edit the seed file
notepad hub_service\mcp-hub.json
# Re-seed (idempotent)
python scripts/seed_hub_db.py
```

**Start a single MCP server manually:**
```powershell
# Customer Intelligence (streamable-HTTP, port 9100)
$env:MCP_SERVER_ID="fab-customer-server"
$env:MCP_TRANSPORT="http"
$env:MCP_HOST="127.0.0.1"
$env:MCP_PORT="9100"
cd datalayer-as-service
python -m mcp_server.customer_server

# Pricing Engine (streamable-HTTP, port 9200)
$env:MCP_SERVER_ID="fab-pricing-server"
$env:MCP_TRANSPORT="http"
$env:MCP_HOST="127.0.0.1"
$env:MCP_PORT="9200"
python -m mcp_server.pricing_server
```

**Generate a JWT for use as a Bearer token:**
```powershell
# Agent JWT (no server scope)
python hub_service/auth.py --sub agent --roles agent --hours 24

# Server-scoped JWT (audience = server ID)
python hub_service/auth.py --sub agent --roles agent --hours 1 --server-id fab-customer-server
```

---

## Running the CLI agent

```powershell
# Customer Intelligence
python agent.py "Show me the 360 profile for CUST001"
python agent.py "What are cross-sell opportunities for CUST002?"

# Pricing
python agent.py "What pricing should I recommend for CUST001?"
python agent.py "Which deals are non-compliant?"

# Multi-server
python agent.py "Comprehensive analysis of CUST001"

# Demo tools
python agent.py "What is the current weather in Tokyo?"
python agent.py "Calculate sqrt(225)"
```

---

## Stopping everything

**PowerShell — kill all hub ports:**
```powershell
foreach ($port in @(8001,8002,8003,8010,8080,8090,9100,9200)) {
  $pids = (netstat -ano | Select-String ":$port " | ForEach-Object { ($_ -split '\s+')[-1] } | Select-Object -Unique)
  foreach ($pid in $pids) { if ($pid -match '^\d+$') { taskkill /PID $pid /F 2>$null } }
}
```

**Git Bash:**
```bash
for port in 8001 8002 8003 8010 8080 8090 9100 9200; do
  pid=$(netstat -ano 2>/dev/null | awk "/0.0.0.0:$port /{print \$NF}" | head -1)
  [ -n "$pid" ] && taskkill //PID "$pid" //F &>/dev/null || true
done
```

---

## Troubleshooting

### MySQL won't start

```powershell
# Start mysqld manually
& "C:\Program Files\MySQL\MySQL Server 8.4\bin\mysqld.exe" --defaults-file="C:\MySQL\my.ini"
```

### Port already in use

```powershell
# Find PID using port (e.g., 9100)
netstat -ano | findstr ":9100"
# Kill it
taskkill /PID <pid> /F
```

### Ollama not responding

```powershell
ollama serve
# Verify
curl http://localhost:11434/api/tags
```

Set `HUB_LLM_ENABLED=false` in `.env` to skip LLM routing and use first-match routing instead (useful when Ollama is unavailable).

### Test/Tools button shows "invalid_token" (400)

The hub mints a short-lived RS256 JWT for probe requests. This error means the RSA key pair is missing or mismatched. Check:
```powershell
ls hub_service\.keys\
# Should show private.pem and public.pem
```

If missing, delete the `.keys` folder and restart the hub — it regenerates the key pair on startup. MCP servers must then be restarted so they re-fetch the JWKS.

### Test/Tools button shows "SSE transport: could not obtain message endpoint"

The DB has `transport='sse'` but the server is actually running streamable-HTTP (or is offline). Either:
- Update the server's transport in Admin UI → Edit
- Or restart the MCP server and verify it is on the correct port

### Test/Tools button shows "406 Not Acceptable"

The MCP server requires `Accept: application/json, text/event-stream`. This is handled automatically by the hub's probe — if you see this, the server is running a non-FastMCP transport. Check the server logs.

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama API URL |
| `OLLAMA_MODEL` | `llama3.2:3b` | Model for LLM routing and tool loops |
| `HUB_HOST` | `0.0.0.0` | Hub bind address |
| `HUB_PORT` | `8090` | Hub port |
| `HUB_LLM_ENABLED` | `true` | `false` = first-match routing, no LLM |
| `HUB_SERVER_URL` | `http://localhost:8090` | Hub URL used by agent |
| `HUB_ADMIN_USERNAME` | `admin` | Admin UI username |
| `HUB_ADMIN_PASSWORD` | `admin` | Admin UI password |
| `HUB_AGENT_USERNAME` | `agent` | Agent login username |
| `HUB_AGENT_PASSWORD` | — | Agent login password |
| `AUTH_ENABLED` | `true` | `false` = bypass all hub auth checks |
| `AUTH_PROVIDER` | `local` | `local` or `azure` |
| `JWT_SECRET` | — | HS256 signing secret (hub + chat share this) |
| `HUB_API_KEY` | — | Static bearer key fallback (optional) |
| `CHAT_PORT` | `8080` | Chat UI port |
| `MCP_AUTH_ENABLED` | `true` | `false` = bypass all MCP server auth |
| `MCP_API_KEY` | — | Shared bearer token sent to all MCP servers |
| `MYSQL_USER` | — | MySQL username (set in `datalayer-as-service/.env`) |
| `MYSQL_PASSWORD` | — | MySQL password |
| `MYSQL_DATABASE` | `fab_semantic` | MySQL database |

---

## Key files

| File | Purpose |
|------|---------|
| `.env` | All secrets — never commit |
| `hub_service/hub_server.py` | Hub API, admin UI, RBAC middleware |
| `hub_service/auth.py` | RS256 key pair, JWT mint/verify, JWKS |
| `hub_service/mcp-hub.json` | Seed source for the `mcp_servers` table |
| `hub_service/.keys/private.pem` | RSA private key — hub signs tokens with this |
| `hub_service/.keys/public.pem` | RSA public key — matches JWKS endpoint |
| `scripts/seed_hub_db.py` | Create + seed `mcp_servers` table (idempotent) |
| `scripts/start_servers.ps1` | Start all 7 servers (PowerShell) |
| `scripts/start_servers.sh` | Start all 7 servers (Git Bash) |
| `scripts/health_check.py` | Check all endpoints |
| `AUTH.md` | Full auth reference (token types, RBAC, troubleshooting) |
| `RUNBOOK.md` | Operational runbook (admin UI guide, key management, trace events) |

---

## Tech stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.13 |
| MCP framework | FastMCP 2.3.4 + mcp >= 1.9.2 |
| LLM | Ollama llama3.2:3b (local, no API key) |
| LLM client | langchain-openai (OpenAI-compatible, pointed at Ollama) |
| Agent loop | langgraph, langchain-mcp-adapters |
| Hub server | FastAPI (uvicorn) — port 8090 |
| Chat UI server | FastAPI (uvicorn) — port 8080 |
| Data layer | SQLAlchemy + MySQL 8.4 |
| SSE servers | FastMCP SSE transport |
| FAB servers | FastMCP streamable-HTTP transport |
