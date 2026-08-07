# FAB MCP Hub — Developer Guide

> **For auth, RBAC, and token management** see [AUTH.md](AUTH.md).  
> **For operations and runbook** see [RUNBOOK.md](RUNBOOK.md).

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Quick Start](#2-quick-start)
3. [Service Reference](#3-service-reference)
   - [Chat Server](#31-chat-server-port-8080)
   - [Hub Server](#32-hub-server-port-8090)
   - [Agent Orchestrator](#33-agent-orchestrator-agentpy)
   - [MCP Servers](#34-mcp-servers)
4. [Configuration Reference](#4-configuration-reference)
5. [Database Schema](#5-database-schema)
6. [Admin Consoles](#6-admin-consoles)
7. [Authentication Flow](#7-authentication-flow)
8. [API Reference](#8-api-reference)
9. [Adding a New MCP Server](#9-adding-a-new-mcp-server)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Architecture Overview

```
Browser (http://localhost:8080)
  │
  │  POST /chat/stream   Bearer: user-JWT
  ▼
┌──────────────────────────────────────────────────┐
│  chat_service/chat_server.py   (FastAPI, :8080)  │
│  • SPA serving chat UI (HTML/CSS/JS inline)      │
│  • Validates user JWT, persists sessions/msgs    │
│  • Streams SSE trace events to browser           │
│  • SQLite: chat_sessions, chat_messages          │
│  • MySQL:  chat_users (passwords, roles)         │
└──────────────────┬───────────────────────────────┘
                   │ calls agent.run_agent(query, hub_token)
                   ▼
┌──────────────────────────────────────────────────┐
│  agent.py   (LangChain ReAct orchestrator)       │
│  • POST /discover → hub → picks MCP server       │
│  • Loads MCP tools live via MCP protocol         │
│  • Runs tool-call loop (Ollama llama3.2:3b)      │
│  • Emits structured trace events (auth/tool/...)  │
└──────────────────┬───────────────────────────────┘
                   │ POST /discover   Bearer: user-JWT or HUB_API_KEY
                   ▼
┌──────────────────────────────────────────────────┐
│  hub_service/hub_server.py   (FastAPI, :8090)    │
│  • Routing gateway: LLM-based or fallback        │
│  • MySQL registry: mcp_servers table             │
│  • 60-second in-process cache                    │
│  • Admin Console: http://localhost:8090/admin    │
└──────┬────────────────────────────────────────── ┘
       │ JSON-RPC over HTTP   Bearer: MCP_API_KEY
       ├──▶ data_server.py      (:8001)
       ├──▶ calc_server.py      (:8002)
       ├──▶ weather_server.py   (:8003)
       ├──▶ customer_server.py  (:9100)
       └──▶ pricing_server.py   (:9200)
                   │
                   ▼
            MySQL fab_semantic
```

**Key design decisions:**

| Decision | Reason |
|---|---|
| Hub reads server registry from MySQL | Allows live add/remove without restart |
| 60-second hub cache | Avoids DB hit on every `/discover` call |
| User JWT forwarded to hub | Hub auth logs show the real user, not a service identity |
| MCP servers get a separate `MCP_API_KEY` | Isolates MCP-layer auth from hub-layer auth |
| Ollama llama3.2:3b for routing | No cloud API key required; runs fully local |

---

## 2. Quick Start

### Prerequisites

- Python 3.11+
- MySQL 8.x running locally (`mysqld` must be started manually after reboot on Windows)
- Ollama with `llama3.2:3b` pulled: `ollama pull llama3.2:3b`

### First run

```bash
# 1. Clone and create virtual environment
python -m venv .venv
.venv/Scripts/activate          # Windows
# source .venv/bin/activate     # Mac/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Seed the MySQL database (one-time)
python scripts/seed_hub_db.py

# 4. Copy and edit environment variables
cp .env.example .env            # then open .env and review

# 5. Start all services
bash Scripts/start_servers.sh   # Linux/Mac
# or: .\Scripts\start_servers.ps1   # Windows PowerShell

# 6. Open the chat UI
# http://localhost:8080
# Login: admin / admin

# 7. Open the Hub Admin Console
# http://localhost:8090/admin
# Login: admin / admin  (or HUB_ADMIN_USERNAME / HUB_ADMIN_PASSWORD from .env)
```

### Directory layout

```
fab-mcp-hub-simple/
├── .env                        ← All secrets and config (never commit)
├── agent.py                    ← LangChain orchestrator
├── AUTH.md                     ← Auth, RBAC, token lifecycle guide
├── DEVELOPER.md                ← This file
├── chat_service/
│   ├── chat_server.py          ← Chat UI + API server (port 8080)
│   └── data/
│       └── fab_chat.db         ← SQLite: sessions, messages, traces
├── hub_service/
│   ├── hub_server.py           ← Hub routing server + admin console (port 8090)
│   ├── auth.py                 ← JWT validation + RBAC for hub
│   ├── db.py                   ← SQLAlchemy engine (MySQL)
│   └── observability.py        ← Structured event ring buffer
├── datalayer-as-service/
│   ├── .env                    ← MySQL creds (overrides root .env)
│   └── mcp_server/
│       ├── auth.py             ← JWT validation + RBAC for MCP servers
│       ├── tools.py            ← All MySQL query functions
│       ├── data_server.py      ← MCP server: deal/risk data  (:8001)
│       ├── calc_server.py      ← MCP server: calculations    (:8002)
│       ├── weather_server.py   ← MCP server: weather data    (:8003)
│       ├── customer_server.py  ← MCP server: customer 360    (:9100)
│       └── pricing_server.py   ← MCP server: pricing engine  (:9200)
└── scripts/
    ├── seed_hub_db.py          ← Populates mcp_servers table
    ├── start_servers.sh
    └── start_servers.ps1
```

---

## 3. Service Reference

### 3.1 Chat Server (port 8080)

**File:** `chat_service/chat_server.py`  
**Entry point:** `python chat_service/chat_server.py`

The chat server is a FastAPI app that serves the entire browser UI as a single inline HTML page. It handles user authentication, session management, and proxies queries through to `agent.py`.

#### Key responsibilities

| Responsibility | How |
|---|---|
| Serve the SPA | Single `GET /` returns the full HTML/CSS/JS inline |
| User authentication | `POST /api/auth/login` validates against MySQL `chat_users` table (falls back to in-memory if MySQL down) |
| Session persistence | SQLite `chat_sessions` + `chat_messages` tables in `chat_service/data/fab_chat.db` |
| Streaming responses | `POST /chat/stream` returns an `EventSourceResponse` (SSE) |
| Trace storage | Structured trace events stored in SQLite `trace_events` table |
| Admin panel | Accessible to `admin`-role users via ⚙️ sidebar button |

#### User authentication details

Passwords are hashed with `pbkdf2:sha256:200000:<salt>:<hash>`. On first startup:
1. Creates the `chat_users` table in MySQL (if it doesn't exist)
2. Seeds it from the default users or `CHAT_USERS` env var (if table is empty)
3. Sets `_USE_DB_USERS = True` — all subsequent auth reads/writes go to MySQL

If MySQL is unavailable at startup, `_USE_DB_USERS = False` and the in-memory dict `_USERS` is used as a fallback. A startup warning is emitted.

#### Default users

| Username | Password | Role |
|---|---|---|
| `admin` | `admin` | admin |
| `analyst` | `analyst` | agent |
| `viewer` | `viewer` | agent |

Override by setting `CHAT_USERS=alice:pass:admin\|bob:pass:agent` in `.env`.

#### Environment variables (chat-specific)

| Variable | Default | Purpose |
|---|---|---|
| `CHAT_HOST` | `0.0.0.0` | Bind address |
| `CHAT_PORT` | `8080` | Listen port |
| `JWT_SECRET` | hardcoded dev value | HS256 signing key for user JWTs; **must match hub** |
| `CHAT_USERS` | (empty) | Optional user list that overrides DB seed |

---

### 3.2 Hub Server (port 8090)

**File:** `hub_service/hub_server.py`  
**Entry point:** `python hub_service/hub_server.py`

The hub is the routing gateway. Every query from `agent.py` goes through `POST /discover`, which uses an Ollama-powered LLM routing agent to pick the best MCP server based on server descriptions in MySQL.

#### Key responsibilities

| Responsibility | How |
|---|---|
| Server registry | MySQL `fab_semantic.mcp_servers` table |
| Routing (LLM) | LangChain ReAct agent with `pick_server()` tool |
| Routing (fallback) | Returns the first active server when LLM is disabled |
| 60-second cache | `_hub_cache` dict avoids DB hit on every request |
| Admin Console | `GET /admin` serves the inline admin HTML page |
| Observability | `hub_service/observability.py` ring buffer; `GET /api/logs` |

#### Routing logic (`route_to_server`)

```
/discover  {"intent": "What is the weather in Dubai?"}
    │
    ▼
load_hub() → {"servers": [{id, name, capability, skills, description}, ...]}
    │
    ├─ HUB_LLM_ENABLED=true  →  LangChain ReAct agent reads server list
    │                            calls pick_server(server_id, reason) 1+ times
    │                            returns list of matched servers
    │
    └─ HUB_LLM_ENABLED=false →  returns first server in list (dev mode)
```

#### Cache invalidation

The 60-second cache is bypassed immediately when:
- Admin creates/updates/deletes a server via the Admin Console
- Admin calls `POST /api/hub/refresh`

#### Auth on startup

`hub_service/auth.py` reads `HUB_API_KEY` and `JWT_SECRET` at **module import time**. The hub server loads `.env` before importing auth to guarantee these values are populated. If you add new secrets to `.env`, restart the hub server.

#### Environment variables (hub-specific)

| Variable | Default | Purpose |
|---|---|---|
| `HUB_HOST` | `0.0.0.0` | Bind address |
| `HUB_PORT` | `8090` | Listen port |
| `HUB_LLM_ENABLED` | `true` | Enable LLM routing (set `false` for fast dev) |
| `AUTH_ENABLED` | `true` | Enable Bearer token validation |
| `AUTH_PROVIDER` | `local` | `local` or `azure` |
| `HUB_API_KEY` | (empty) | Static bearer key for agent-to-hub auth |
| `JWT_SECRET` | (empty) | HS256 secret; shared with chat server |
| `HUB_ADMIN_USERNAME` | `admin` | Hub Admin Console username |
| `HUB_ADMIN_PASSWORD` | `admin` | Hub Admin Console password |
| `HUB_SERVER_URL` | `http://localhost:8090` | Self-URL (used by agent.py) |

---

### 3.3 Agent Orchestrator (`agent.py`)

**File:** `agent.py` (project root)  
**Not a standalone server** — imported and called by `chat_server.py`

`agent.py` is the LangChain-based orchestration layer. It runs as part of the chat server process (not a separate service).

#### What it does

```python
result = await run_agent(query, hub_token="eyJ...")
```

1. `POST /discover` to hub with the user query → gets server list + routing reason
2. Connects to each selected MCP server using the MCP protocol
3. Discovers tools live (`tools/list` JSON-RPC call)
4. Runs a ReAct tool-call loop using Ollama
5. Returns the final answer string

#### Tracing

`agent.py` emits structured trace events during execution. These are collected by `chat_server.py` and stored in SQLite `trace_events`. The browser polls for them and renders them in the 4-tab trace panel (Timeline / Graph / Security / Perf).

#### Key configuration

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama endpoint |
| `OLLAMA_MODEL` | `llama3.2:3b` | Model for tool-call loop |
| `HUB_SERVER_URL` | `http://localhost:8090` | Hub to route through |
| `HUB_API_KEY` | (empty) | Bearer token for hub requests |
| `MCP_API_KEY` | (empty) | Bearer token for MCP server requests |

---

### 3.4 MCP Servers

All MCP servers follow the same pattern. Each is a standalone FastMCP application that exposes domain-specific tools over HTTP.

#### Servers

| Server | Port | Transport | Domain |
|---|---|---|---|
| `data_server.py` | 8001 | streamable-http | Deal data, risk ratings |
| `calc_server.py` | 8002 | streamable-http | Calculations, analytics |
| `weather_server.py` | 8003 | streamable-http | Weather data |
| `customer_server.py` | 9100 | streamable-http | Customer 360 |
| `pricing_server.py` | 9200 | streamable-http | Pricing engine |

#### How MCP auth works

Each server loads `.env` **before** importing `auth.py`, which reads `MCP_API_KEY` and `MCP_JWT_SECRET` at module level:

```python
# In every server entry point (e.g. customer_server.py)
from dotenv import load_dotenv
_here = pathlib.Path(__file__).resolve().parent
load_dotenv(_here.parent.parent / ".env")          # root .env (MCP auth keys)
load_dotenv(_here.parent / ".env", override=True)  # datalayer-as-service/.env (MySQL creds)

from mcp_server.auth import mcp_middleware, MCP_AUTH_ENABLED
```

If this loading happens after the import, `MCP_API_KEY` is empty and the server enters open dev mode (warning emitted). **Always keep the dotenv block before the auth import.**

#### Adding per-tool RBAC

```python
from mcp_server.auth import require_role

@mcp.tool()
def sensitive_tool(customer_id: str) -> str:
    require_role("agent")     # raises PermissionError if caller lacks this role
    ...
```

#### MCP server environment variables

| Variable | Default | Purpose |
|---|---|---|
| `MCP_AUTH_ENABLED` | `true` | Enable MCP-layer auth |
| `MCP_AUTH_PROVIDER` | `local` | `local` only (Azure not yet implemented at MCP layer) |
| `MCP_API_KEY` | (empty) | Static bearer key or JWT |
| `MCP_JWT_SECRET` | (empty) | HS256 secret for validating JWTs at MCP layer |
| `MCP_TRANSPORT` | `stdio` | `http` or `streamable-http` for network mode |
| `MCP_HOST` | `127.0.0.1` | Bind address |
| `MCP_PORT` | (server-specific) | Listen port |

---

## 4. Configuration Reference

All configuration lives in the root `.env` file. Load order:

1. Root `.env` is loaded by all services at startup
2. `datalayer-as-service/.env` is loaded **after** (with `override=True`) by MCP servers only — provides MySQL credentials

### Complete `.env` reference

```bash
# ── Hub Server ───────────────────────────────────────────────────────────────
HUB_HOST=0.0.0.0
HUB_PORT=8090
HUB_LLM_ENABLED=true         # Set false to skip Ollama and return first server
HUB_SERVER_URL=http://localhost:8090

# ── Hub Admin Console ────────────────────────────────────────────────────────
HUB_ADMIN_USERNAME=admin      # Username for http://localhost:8090/admin
HUB_ADMIN_PASSWORD=admin      # Change this in production!

# ── Hub Authentication ───────────────────────────────────────────────────────
AUTH_ENABLED=true
AUTH_PROVIDER=local           # local | azure
HUB_API_KEY=<jwt-or-hex>      # Agent-to-hub bearer token
JWT_SECRET=<32-byte-hex>      # HS256 key shared between Chat and Hub

# ── Chat Server ──────────────────────────────────────────────────────────────
CHAT_HOST=0.0.0.0
CHAT_PORT=8080
# CHAT_USERS=alice:pass:admin|bob:pass:agent   # Overrides MySQL seed (optional)

# ── MCP Servers ──────────────────────────────────────────────────────────────
MCP_AUTH_ENABLED=true
MCP_AUTH_PROVIDER=local
MCP_API_KEY=<jwt-or-hex>      # Agent-to-MCP bearer token
MCP_JWT_SECRET=<32-byte-hex>  # HS256 key for MCP JWTs (can equal JWT_SECRET)

# ── LLM (Ollama) ─────────────────────────────────────────────────────────────
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_MODEL=llama3.2:3b

# ── MySQL ─────────────────────────────────────────────────────────────────────
MYSQL_USER=test_user
MYSQL_PASSWORD=Welcome@12345
MYSQL_DATABASE=fab_semantic
# MYSQL_HOST=localhost         # default
# MYSQL_PORT=3306              # default
```

### Generating strong secrets

```bash
# Generate JWT_SECRET or MCP_JWT_SECRET
python -c "import secrets; print(secrets.token_hex(32))"

# Generate service JWTs (better than raw hex strings — richer auth logs)
python hub_service/auth.py --sub fab-agent --roles agent --hours 8760
# → copy output to HUB_API_KEY

python datalayer-as-service/mcp_server/auth.py --sub fab-agent --roles agent --hours 8760
# → copy output to MCP_API_KEY

# Or use the Hub Admin Console token generator:
# http://localhost:8090/admin → Auth & Tokens tab
```

---

## 5. Database Schema

### MySQL (`fab_semantic`)

#### `mcp_servers` — Hub server registry

```sql
CREATE TABLE mcp_servers (
    id           VARCHAR(128) PRIMARY KEY,    -- e.g. "fab-customer-server"
    name         VARCHAR(256),                -- Display name
    endpoint     TEXT,                        -- Full URL: .../sse or .../mcp/
    transport    VARCHAR(32),                 -- "sse" or "streamable-http"
    capability   TEXT,                        -- One-line domain summary (used by LLM router)
    skills       JSON,                        -- ["weather", "forecast", ...]
    description  TEXT,                        -- Full description (used by LLM router)
    examples     JSON,                        -- ["What is the weather in Dubai?", ...]
    start_cmd    TEXT,                        -- Shell command to launch (informational)
    is_active    TINYINT(1) DEFAULT 1         -- 0 = disabled without deleting
);
```

#### `mcp_server_changelog` — Audit trail for server changes

```sql
CREATE TABLE mcp_server_changelog (
    id           BIGINT AUTO_INCREMENT PRIMARY KEY,
    server_id    VARCHAR(128),
    action       VARCHAR(32),       -- "create" | "update" | "delete"
    changed_by   VARCHAR(64),       -- JWT sub of the admin who made the change
    changed_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    before_state JSON,              -- Full server record before change (null for create)
    after_state  JSON               -- Full server record after change (null for delete)
);
```

#### `chat_users` — Chat server user store

```sql
CREATE TABLE chat_users (
    username             VARCHAR(64) PRIMARY KEY,
    display_name         VARCHAR(128) NOT NULL,
    password_hash        VARCHAR(255) NOT NULL,  -- pbkdf2:sha256:200000:<salt>:<hash>
    roles                JSON NOT NULL,           -- ["admin"] or ["agent"]
    is_active            TINYINT(1) DEFAULT 1,
    auth_provider        VARCHAR(32) DEFAULT 'local',  -- 'local' | 'azure_ad' (future)
    must_change_password TINYINT(1) DEFAULT 0,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    created_by           VARCHAR(64)
);
```

**Future SSO adaptation:** Add a row with `auth_provider='azure_ad'` for each SSO user. The login endpoint checks `auth_provider` and routes to the appropriate validation path.

### SQLite (`chat_service/data/fab_chat.db`)

#### `chat_sessions`

```sql
CREATE TABLE chat_sessions (
    id         TEXT PRIMARY KEY,    -- UUID
    name       TEXT,
    username   TEXT,
    created_at TEXT,
    updated_at TEXT
);
```

#### `chat_messages`

```sql
CREATE TABLE chat_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    role       TEXT,    -- "user" | "assistant"
    content    TEXT,
    created_at TEXT,
    msg_index  INTEGER
);
```

#### `trace_events`

```sql
CREATE TABLE trace_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    msg_index  INTEGER,
    event_type TEXT,
    data       TEXT,    -- JSON
    created_at TEXT
);
```

---

## 6. Admin Consoles

### Hub Admin Console — `http://localhost:8090/admin`

**Login:** Enter `HUB_ADMIN_USERNAME` / `HUB_ADMIN_PASSWORD` from `.env` (default: `admin`/`admin`).  
Advanced users can click "Use token instead →" and paste a raw admin JWT.

| Tab | What you can do |
|---|---|
| **Dashboard** | View hub status, LLM mode, active server list |
| **MCP Servers** | Add, edit, delete, enable/disable servers; **Test** (HTTP health check with auth); **Tools** (JSON-RPC `tools/list` — lists all tools the server exposes); **History** (full changelog with before/after JSON) |
| **Observability** | Filter and browse auth/routing/request/admin events; auto-refresh |
| **Auth & Tokens** | Generate admin or service JWTs; JWT anatomy explainer |

**Test button** — probes `<base-url>/health` with `MCP_API_KEY` auth header. Requires MCP server to be running.  
**Tools button** — calls `POST <endpoint>` with JSON-RPC `tools/list`. Returns every tool name and description.  
**Refresh Cache** — forces the next `/discover` call to re-read MySQL immediately (bypasses 60-second TTL).

### Chat Admin Panel — ⚙️ sidebar button (admin role only)

| Section | What you can do |
|---|---|
| **System Config** | View hub URL, auth mode, key status |
| **User Management** | Add users (auto-generates password if blank), edit display/role/active status, delete; all writes go to MySQL `chat_users` |
| **Generate Token** | Mint JWTs (for service-to-service use) |
| **Change Password** | Any logged-in user can change their password via the ⚙️ → Change Password button in the sidebar footer |

**Generated password flow:** If admin creates a user without entering a password, a random 12-character password is generated and shown once. The `must_change_password` flag is automatically set so the user is prompted to change it on first login.

---

## 7. Authentication Flow

See [AUTH.md](AUTH.md) for the full reference. Quick summary:

```
Browser login:
  POST /api/auth/login  {username, token(=password)}
  → validates against chat_users in MySQL
  → returns JWT:  {sub, roles, iss="fab-chat", exp=+8h}

Chat → Hub:
  Authorization: Bearer <user-JWT>
  Hub validates with JWT_SECRET (same secret as chat minted it with)

Agent → Hub:
  Authorization: Bearer <HUB_API_KEY>
  (static key or JWT with sub=fab-agent, roles=[agent])

Agent → MCP:
  Authorization: Bearer <MCP_API_KEY>
  (static key or JWT validated by mcp_server/auth.py using MCP_JWT_SECRET)
```

**RBAC roles:**

| Role | Chat | Hub | MCP |
|---|---|---|---|
| `admin` | Admin panel, user management, token generation | All endpoints, logs, server CRUD | All tools (bypasses `require_role`) |
| `agent` | Chat, sessions, history | `/discover`, `/servers`, `/health` | Standard tool access |
| `readonly` | (not used in chat) | `/servers`, `/health` only | (not used) |

---

## 8. API Reference

### Chat Server (`:8080`)

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/` | — | Serve the SPA |
| `POST` | `/api/auth/login` | — | Login: `{username, token}` → `{token, sub, roles, display}` |
| `POST` | `/api/auth/change-password` | Bearer JWT | Change own password: `{current_password, new_password}` |
| `POST` | `/chat/stream` | Bearer JWT | Stream SSE chat response |
| `GET` | `/api/sessions` | Bearer JWT | List user's sessions |
| `DELETE` | `/api/sessions/{id}` | Bearer JWT | Delete session |
| `GET` | `/api/sessions/{id}/messages` | Bearer JWT | Get messages |
| `GET` | `/api/sessions/{id}/trace/{idx}` | Bearer JWT | Get trace events for message |
| `GET` | `/api/dashboard` | Bearer JWT | Stats + recent sessions |
| `GET` | `/api/logs` | admin JWT | Fetch hub observability logs |
| `GET` | `/api/search` | Bearer JWT | Full-text search |
| `GET` | `/api/admin/config` | admin JWT | System config status |
| `GET` | `/api/admin/users` | admin JWT | List all users |
| `POST` | `/api/admin/users` | admin JWT | Create user |
| `PUT` | `/api/admin/users/{username}` | admin JWT | Update user |
| `DELETE` | `/api/admin/users/{username}` | admin JWT | Delete user |
| `POST` | `/api/admin/token` | admin JWT | Mint a JWT |

### Hub Server (`:8090`)

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | — | Service status + server list |
| `GET` | `/servers` | agent+ | All active servers |
| `GET` | `/servers/all` | admin | All servers inc. inactive |
| `GET` | `/servers/{id}` | agent+ | Single server config |
| `POST` | `/servers` | admin | Register new server |
| `PUT` | `/servers/{id}` | admin | Update server config |
| `DELETE` | `/servers/{id}` | admin | Remove server |
| `POST` | `/servers/{id}/test` | admin | HTTP health check |
| `GET` | `/servers/{id}/tools` | admin | List MCP tools via JSON-RPC |
| `POST` | `/discover` | agent+ | Route intent → server list |
| `GET` | `/api/logs` | admin | Observability event log |
| `GET` | `/api/servers/changelog` | admin | Server change audit log |
| `POST` | `/api/hub/refresh` | admin | Invalidate hub cache |
| `POST` | `/api/auth/token` | admin | Mint a JWT |
| `POST` | `/api/admin/login` | — | Username/password → admin JWT |
| `GET` | `/admin` | — | Hub Admin Console HTML |

---

## 9. Adding a New MCP Server

### Step 1 — Write the server

```python
# datalayer-as-service/mcp_server/my_server.py
import os, pathlib, json, logging
from typing import Any

# Load .env BEFORE importing auth (auth reads env vars at module level)
try:
    from dotenv import load_dotenv as _ld
    _here = pathlib.Path(__file__).resolve().parent
    _ld(_here.parent.parent / ".env")
    _ld(_here.parent / ".env", override=True)
except ImportError:
    pass

import uvicorn
from fastmcp import FastMCP
from mcp_server.auth import mcp_middleware, MCP_AUTH_ENABLED
from mcp_server.tools import query_my_domain   # your DB query function

mcp = FastMCP(name="My Domain MCP Server",
              instructions="Describe what this server does for the LLM router.")

@mcp.tool()
def my_tool(param: str = "") -> str:
    """One-line docstring — shown to the LLM agent."""
    return json.dumps(query_my_domain(param), default=str)

if __name__ == "__main__":
    port = int(os.getenv("MCP_PORT", "8005"))
    app = mcp.streamable_http_app(middleware=mcp_middleware())
    uvicorn.run(app, host=os.getenv("MCP_HOST", "127.0.0.1"), port=port, log_level="warning")
```

### Step 2 — Register in MySQL

```sql
INSERT INTO mcp_servers (id, name, endpoint, transport, capability, skills, description, examples, start_cmd, is_active)
VALUES (
  'my-domain-server',
  'My Domain Server',
  'http://127.0.0.1:8005/mcp/',
  'streamable-http',
  'One-line domain summary for LLM routing',
  '["feature1", "feature2"]',
  'Full description of what this server provides, used by the LLM routing agent.',
  '["Example question 1?", "Example question 2?"]',
  'python -m mcp_server.my_server',
  1
);
```

Or use the Hub Admin Console → MCP Servers → Add Server.

### Step 3 — Start it

```bash
MCP_TRANSPORT=http MCP_HOST=127.0.0.1 MCP_PORT=8005 python -m mcp_server.my_server
```

Then click **Refresh Cache** in the Hub Admin Console.

### Step 4 — Validate

In the Hub Admin Console → MCP Servers, click **Test** to verify connectivity and **Tools** to confirm all tools are discoverable.

---

## 10. Troubleshooting

### MCP server warns `⚠ MCP SECURITY: ... open dev mode`

The `MCP_API_KEY` / `MCP_JWT_SECRET` weren't in `os.environ` when `auth.py` was imported.

**Fix:** Ensure the dotenv loading block appears **before** any `from mcp_server.auth import ...` line in the server entry point.

```python
# CORRECT — dotenv first
try:
    from dotenv import load_dotenv as _ld
    import pathlib as _pl
    _ld(_pl.Path(__file__).resolve().parent.parent / ".env")
    _ld(_pl.Path(__file__).resolve().parent / ".env", override=True)
except ImportError:
    pass
from mcp_server.auth import mcp_middleware  # reads env at import time
```

### `python hub_service/auth.py` fails with `RuntimeError: Set JWT_SECRET`

`auth.py` now loads `.env` automatically when run as a script. If it still fails, check that `JWT_SECRET` is set in your root `.env` and the file is in the project root (parent of `hub_service/`).

### Hub Admin Console — MCP Servers tab is empty

Either MySQL is not running, or the `mcp_servers` table is empty. Check:

```bash
# Is MySQL running?
mysqladmin -u test_user -pWelcome@12345 status

# Are there servers in the table?
mysql -u test_user -pWelcome@12345 fab_semantic -e "SELECT id, is_active FROM mcp_servers;"

# Re-seed if empty:
python scripts/seed_hub_db.py
```

### Test button returns 401

The hub probes MCP servers with `MCP_API_KEY` from `.env`. If `MCP_API_KEY` is empty, the probe has no auth header and the MCP server rejects it.

**Fix:** Set `MCP_API_KEY` in `.env` to the same value the agent uses.

### Tools button returns error

The hub POSTs JSON-RPC `tools/list` to the MCP server endpoint with `MCP_API_KEY` auth. If it fails:
1. Check the server is running: `MCP_PORT=9100 python -m mcp_server.customer_server`
2. Check `MCP_API_KEY` is correct in `.env`
3. Verify the endpoint URL in the `mcp_servers` table ends with `/mcp/` (not `/sse`)

### Edit user / Edit server button does nothing

This was a JavaScript double-stringify bug. The fix stores server/user data in a JS map (`_srvMap`, `_userMap`) keyed by ID, and only passes the ID string in `onclick`. If you see this again, check that `loadSrv()`/`loadAdminUsers()` populates the map before the edit button is clicked.

### Chat users reset on restart

If MySQL is unavailable at startup, `_USE_DB_USERS = False` and any users added via the admin panel are in-memory only. Check:

```bash
# Is MySQL running and accessible from Python?
python -c "
from sqlalchemy import create_engine, text
e = create_engine('mysql+mysqlconnector://test_user:Welcome@12345@localhost/fab_semantic')
print(e.connect().execute(text('SELECT 1')).fetchone())
"
```

### `eyJhbGciOi...` — all tokens look the same

This is expected. See [AUTH.md — FAQ](AUTH.md) for a full explanation. All HS256 JWTs share the same header segment. The payload and signature are unique per token and contain the actual user identity.
