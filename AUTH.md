# FAB MCP Hub — Authentication & Authorization Reference

## Architecture Overview

```
Browser / Agent
    │
    │  POST /api/auth/login  (username + password)
    ▼
Chat Server (HS256 JWT, iss=fab-chat, JWT_SECRET)
    │
    │  POST /chat/stream     (Bearer HS256-JWT)
    ▼
Hub Server — verifies HS256 via JWT_SECRET
    │
    │  POST /discover        (Bearer HS256-JWT)
    ▼
Hub Server — selects MCP servers, mints RS256 per-server JWTs
    │
    │  tool call             (Bearer RS256-JWT, aud=<server_id>)
    ▼
MCP Server — FastMCP JWTVerifier validates RS256 via JWKS
```

Three token types flow through the system:

| Token | Algorithm | Signed by | Validated by | Audience |
|-------|-----------|-----------|--------------|----------|
| Chat session JWT | HS256 | Chat server (`JWT_SECRET`) | Hub server (`JWT_SECRET`) | none (skipped) |
| Hub admin JWT | RS256 | Hub server (RSA private key) | Hub server (RSA public key) | none |
| Per-server JWT | RS256 | Hub server (RSA private key) | MCP server (JWKS endpoint) | `<server_id>` |

---

## How This Maps to the Standard JWT-for-MCP Pattern

The standard recipe for JWT-authenticated MCP is:

> 1. **Auth service** — issues JWTs, publishes RSA public key as JWKS  
> 2. **MCP server** — validates JWTs via FastMCP `JWTVerifier`  
> 3. **Client** — logs in once, attaches token to every MCP call

This project implements that pattern exactly. Here is the component-by-component mapping:

| Generic role | This implementation | File |
|---|---|---|
| Auth service (FastAPI) | Hub server | `hub_service/hub_server.py` |
| Issues JWTs | `generate_server_token(server_id)` | `hub_service/auth.py` |
| RSA key pair (generate once) | Auto-generated on first hub start | `hub_service/.keys/private.pem` + `public.pem` |
| Publishes public key (JWKS) | `GET /.well-known/jwks.json` | `hub_service/hub_server.py` |
| MCP server (FastMCP) | Customer / Pricing MCP servers | `datalayer-as-service/mcp_server/customer_server.py` |
| `JWTVerifier(jwks_uri, issuer, audience)` | `build_jwt_verifier()` | `datalayer-as-service/mcp_server/auth.py` |
| `FastMCP(auth=…)` | `FastMCP(name=…, auth=build_jwt_verifier())` | `customer_server.py` line 73 |
| Client | Agent | `agent.py` |
| "Logs in once" | `_get_hub_token()` → `POST /auth/login` | `agent.py` |
| "Gets per-server tokens" | `POST /discover` → per-server JWTs | `agent.py → run_agent()` |
| "Attaches token to every call" | `mcp_session()` → `headers={"Authorization":"Bearer …"}` | `agent.py` |

### Step-by-step trace: agent calls a tool on `fab-customer-server`

**Step 1 — RSA key pair (generated once, on first hub start)**

```
hub_service/auth.py → _ensure_key_pair()
    openssl genrsa → hub_service/.keys/private.pem  (RSA-2048, never leaves hub)
                   → hub_service/.keys/public.pem   (published via JWKS endpoint)
```

Equivalent to:
```bash
openssl genrsa -out private.pem 2048
openssl rsa -in private.pem -pubout -out public.pem
```

**Step 2 — Hub publishes the public key as JWKS**

```
GET http://localhost:8090/.well-known/jwks.json
Response: {"keys":[{"kty":"RSA","kid":"hub-rsa-1","use":"sig","alg":"RS256","n":"…","e":"AQAB"}]}
```

Any MCP server can fetch this URL to verify hub-signed tokens without sharing the private key.

**Step 3 — MCP server registers the JWTVerifier on startup**

```python
# datalayer-as-service/mcp_server/auth.py → build_jwt_verifier()

from fastmcp.server.auth.providers.jwt import JWTVerifier

JWTVerifier(
    jwks_uri = "http://localhost:8090/.well-known/jwks.json",
    issuer   = "fab-mcp-hub",           # must match hub's HUB_JWT_ISSUER
    audience = "fab-customer-server",   # set via MCP_SERVER_ID env var
)

# datalayer-as-service/mcp_server/customer_server.py

mcp = FastMCP(name="FAB Customer Intelligence MCP Server",
              auth=build_jwt_verifier())
```

FastMCP wraps every incoming HTTP request — before any tool function runs — and
validates the Bearer token against the JWKS endpoint.

**Step 4 — Agent logs in to the hub (once per process)**

```
POST http://localhost:8090/auth/login
Body: {"username": "agent", "password": "…"}

Hub:  verify credentials → sign RS256 JWT with private.pem
      payload: {"sub":"agent","roles":["agent"],"iss":"fab-mcp-hub","exp":…}

Response: {"access_token": "eyJhbGciOiJSUzI1NiIsImtpZCI6Imh1Yi1yc2EtMSJ9…"}
```

This is the `_get_hub_token()` call in `agent.py`. The token is cached in
`_hub_token_cache` for the process lifetime (not a per-request login).

**Step 5 — Agent calls /discover to route the query and get per-server JWTs**

```
POST http://localhost:8090/discover
Authorization: Bearer <hub-jwt>
Body: {"intent": "Show customer 360 for CUST001"}

Hub:  validate hub-jwt → LLM routing → selects fab-customer-server
      mint NEW RS256 JWT for that server:
        sub=agent, roles=[agent], iss=fab-mcp-hub,
        aud="fab-customer-server",   ← audience is server-specific
        exp=now+1h

Response: [{
  "id":           "fab-customer-server",
  "endpoint":     "http://127.0.0.1:9100/mcp",
  "transport":    "streamable-http",
  "server_token": "eyJhbGciOiJSUzI1NiIsImtpZCI6Imh1Yi1yc2EtMSJ9…"  ← per-server JWT
}]
```

The per-server JWT is **different** from the hub JWT — it has `aud="fab-customer-server"`
so the target server can reject tokens issued for any other server.

**Step 6 — Agent opens an MCP session with the per-server JWT (the `mcp_session()` call)**

```
streamablehttp_client("http://127.0.0.1:9100/mcp",
                       headers={"Authorization": "Bearer <per-server-jwt>"})

POST /mcp  {"method": "initialize", …}   Authorization: Bearer <per-server-jwt>
  → JWTVerifier: fetch JWKS, verify RS256 signature, check iss+aud+exp  → PASS
  → BearerClaimsMiddleware: decode claims → store in ContextVar
  → MCP handshake: {"result": {"protocolVersion": "…", "capabilities": {…}}}

POST /mcp  {"method": "tools/list",  …}   Authorization: Bearer <per-server-jwt>
  → JWTVerifier: validate again → PASS
  → Response: list of 9 tools

POST /mcp  {"method": "tools/call", "params": {"name": "customer_360", "arguments": {"customer_id": "CUST001"}}}
                                              Authorization: Bearer <per-server-jwt>
  → JWTVerifier: validate again → PASS
  → BearerClaimsMiddleware: claims = {"sub":"agent","roles":["agent"],"aud":"fab-customer-server"}
  → require_role("admin","agent") → PASS  (caller has "agent" role)
  → audit_log("customer_360", …) → prints structured log with agent identity
  → query_customer_360("CUST001") → MySQL query with MYSQL_USER/PASSWORD (not the JWT)
  → Response: JSON customer data
```

The JWT is validated on **every HTTP request** — not just on connection. FastMCP does not
maintain a session-level trust state; each request must carry and pass the token independently.

**Step 7 — Key security property: audience scoping prevents cross-server replay**

```
Per-server JWT for fab-customer-server:
  {"aud": "fab-customer-server", "sub": "agent", …}

Sent to fab-pricing-server (port 9200):
  JWTVerifier(audience="fab-pricing-server") → audience mismatch → 401 Unauthorized

The same token cannot be used against a different server,
even though both servers trust the same hub JWKS endpoint.
```

---

## 1. Hub Admin Login

The Hub Admin Console at `http://localhost:8090/admin` uses username/password login.

**Credentials** (set in `.env`):
```
HUB_ADMIN_USERNAME=admin
HUB_ADMIN_PASSWORD=admin
```

**API endpoint:**
```
POST http://localhost:8090/api/admin/login
Content-Type: application/json

{"username": "admin", "password": "admin"}
```

**Response:**
```json
{
  "token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6Imh1Yi1yc2EtMSJ9...",
  "sub": "admin",
  "roles": ["admin"],
  "dev_mode": false
}
```

**Using the token:**
```
GET http://localhost:8090/api/logs
Authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6Imh1Yi1yc2EtMSJ9...
```

---

## 2. Agent Login (RS256 JWT)

Agents (scripts, services) authenticate with the hub to get a long-lived RS256 JWT.

**Credentials** (set in `.env`):
```
HUB_AGENT_USERNAME=admin
HUB_AGENT_PASSWORD=admin
```

**Login:**
```bash
curl -s -X POST http://localhost:8090/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}' \
  | python -m json.tool
```

**Response:**
```json
{
  "token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6Imh1Yi1yc2EtMSJ9.eyJzdWIiOiJhZG1pbiIsInJvbGVzIjpbImFkbWluIl0sImlhdCI6MTc4NTg1NzQ2MSwiZXhwIjoxNzg1OTQzODYxLCJpc3MiOiJmYWItbWNwLWh1YiJ9.SIGNATURE",
  "sub": "admin",
  "roles": ["admin"],
  "expires_in": 86400,
  "token_type": "Bearer"
}
```

**Decode the token (without verifying signature):**
```bash
# Decode header
echo "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6Imh1Yi1yc2EtMSJ9" \
  | base64 -d 2>/dev/null
# {"alg":"RS256","typ":"JWT","kid":"hub-rsa-1"}

# Decode payload
echo "eyJzdWIiOiJhZG1pbiIsInJvbGVzIjpbImFkbWluIl0sImlhdCI6MTc4NTg1NzQ2MSwiZXhwIjoxNzg1OTQzODYxLCJpc3MiOiJmYWItbWNwLWh1YiJ9" \
  | base64 -d 2>/dev/null
# {"sub":"admin","roles":["admin"],"iat":1785857461,"exp":1785943861,"iss":"fab-mcp-hub"}
```

**Mint a token directly (no login required):**
```bash
python hub_service/auth.py \
  --sub agent \
  --roles agent \
  --hours 24

# Output:
# Generated JWT (valid 24h, sub='agent', roles=['agent']):
# eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6Imh1Yi1yc2EtMSJ9...
```

**Mint a scoped token for a specific MCP server:**
```bash
python hub_service/auth.py \
  --sub agent \
  --roles agent \
  --hours 1 \
  --server-id fab-customer-server

# The resulting JWT has:  "aud": "fab-customer-server"
# This token is ONLY accepted by the fab-customer-server MCP endpoint.
```

---

## 3. Chat Server Login (HS256 JWT)

Users log in to the Chat UI at `http://localhost:8080`.

**Default users:**
| Username | Password | Roles |
|----------|----------|-------|
| admin | admin | admin |
| analyst | analyst | agent |
| viewer | viewer | readonly |

**Login endpoint:**
```bash
curl -s -X POST http://localhost:8080/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","token":"admin"}' \
  | python -m json.tool
```

**Response:**
```json
{
  "ok": true,
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhZG1pbiIsInJvbGVzIjpbImFkbWluIl0sImlhdCI6MTc4NTg1NzQ2MSwiZXhwIjoxNzg1OTQzODYxLCJpc3MiOiJmYWItY2hhdCJ9.HMAC_SIGNATURE",
  "sub": "admin",
  "roles": ["admin"],
  "display": "Admin"
}
```

**Token claims:**
```json
{
  "sub":   "admin",
  "roles": ["admin"],
  "iat":   1785857461,
  "exp":   1785943861,
  "iss":   "fab-chat"
}
```

The chat JWT (`iss=fab-chat`) is accepted by the Hub because they share `JWT_SECRET` and the hub does **not** enforce a default issuer for HS256 tokens — the shared secret is the trust anchor.

---

## 4. Discover API (per-server JWTs)

When the chat UI sends a query, it calls `POST /discover` on the hub with its HS256 chat JWT. The hub:
1. Verifies the JWT using `JWT_SECRET`
2. Picks relevant MCP servers via LLM routing
3. Mints a **new RS256 JWT per server** with `aud=<server_id>`
4. Returns the server list with per-server tokens

**Request:**
```bash
CHAT_TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."

curl -s -X POST http://localhost:8090/discover \
  -H "Authorization: Bearer $CHAT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"intent": "Get customer CUST002 details"}' \
  | python -m json.tool
```

**Response (abbreviated):**
```json
{
  "servers": [
    {
      "id": "fab-customer-server",
      "endpoint": "http://localhost:8001/mcp/",
      "transport": "streamable-http",
      "token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6Imh1Yi1yc2EtMSJ9...",
      "token_type": "Bearer"
    }
  ],
  "intent": "Get customer CUST002 details"
}
```

The per-server token has `"aud": "fab-customer-server"` — it will be **rejected** by any other MCP server (audience mismatch).

---

## 5. MCP Server Authentication

MCP servers validate RS256 tokens using the hub's JWKS endpoint.

**Admin UI probe tokens:** The Admin UI **Test** and **Tools** buttons also use this RS256 JWT flow. When you click either button, the hub mints a short-lived token (`sub=hub-admin-probe`, `roles=[admin]`, `aud=<server_id>`, 1-hour lifetime) and sends it as the Bearer token to the MCP server. This is the same validation path the agent uses — no separate "admin key" is needed.

**JWKS endpoint:**
```
GET http://localhost:8090/.well-known/jwks.json
```

**Response:**
```json
{
  "keys": [
    {
      "kty": "RSA",
      "kid": "hub-rsa-1",
      "use": "sig",
      "alg": "RS256",
      "n":   "0vx7agoebGcQSuuPiLJXZptN9nndrQmbXEps2aiAFbWhM78LhWx4...",
      "e":   "AQAB"
    }
  ]
}
```

MCP servers are configured via `FastMCP(auth=JWTVerifier(jwks_uri=..., issuer=..., audience=...))`.

**Verify a per-server token:**
```bash
SERVER_TOKEN="eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6Imh1Yi1yc2EtMSJ9..."

python -c "
import jwt, pathlib, json
pub = pathlib.Path('hub_service/.keys/public.pem').read_text()
payload = jwt.decode(
    '$SERVER_TOKEN',
    pub,
    algorithms=['RS256'],
    options={'verify_aud': False}
)
print(json.dumps(payload, indent=2))
"
```

---

## 6. Static API Key (fallback)

The `HUB_API_KEY` in `.env` is a pre-shared static bearer token accepted by the hub.

```bash
curl -s http://localhost:8090/servers \
  -H "Authorization: Bearer 26805607f0be83760e..."
```

MCP servers have their own `MCP_API_KEY` stored per-server in MySQL (or `.env` fallback). The hub injects the correct key when building the per-server token response.

---

## 7. RBAC Roles

| Role | Permissions |
|------|-------------|
| `admin` | All endpoints: `GET /api/logs`, `POST /discover`, `GET /servers`, admin CRUD |
| `agent` | `POST /discover`, `GET /servers`, `GET /servers/{id}`, `GET /health` |
| `readonly` | `GET /servers`, `GET /servers/{id}`, `GET /health` only |

Roles are embedded in the JWT payload as a list:
```json
{"sub": "admin", "roles": ["admin"], ...}
```

---

## 8. Observability & Logs

### Log files

| File | Service | Format | Contents |
|------|---------|--------|----------|
| `logs/hub.log` | Hub | JSONL | All auth checks, requests, routing, admin actions |
| `logs/chat.log` | Chat | JSONL | Login events, chat starts, per-event traces |

### Hub auth event (logs/hub.log)

Every Bearer token check writes an `auth` event:
```json
{
  "ts": 1785857461.009,
  "type": "auth",
  "valid": true,
  "sub": "admin",
  "roles": ["admin"],
  "token_type": "jwt",
  "iss": "fab-chat",
  "provider": "local",
  "endpoint": "/discover",
  "method": "POST",
  "bearer_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhZG1pbiJ9..."
}
```

### Chat auth event (logs/chat.log)

Login and chat-start events:
```json
{"ts": 1785857465.123, "service": "chat", "type": "auth", "valid": true,
 "sub": "admin", "roles": ["admin"], "endpoint": "/api/auth/login",
 "iss": "fab-chat",
 "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."}

{"ts": 1785857470.456, "service": "chat", "type": "chat_start",
 "session_id": "550e8400-e29b-41d4-a716-446655440000",
 "sub": "admin", "query": "Give me details on CUST002",
 "bearer_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."}
```

### Read logs via API (admin token required)

```bash
ADMIN_TOKEN="eyJhbGciOiJSUzI1NiIs..."

# Last 100 hub events
curl -s "http://localhost:8090/api/logs?n=100" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python -m json.tool

# Auth events only
curl -s "http://localhost:8090/api/logs?n=50&event_type=auth" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python -m json.tool

# Read hub.log file directly via API
curl -s "http://localhost:8090/api/logs/file?n=200" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | python -m json.tool
```

### Tail log files in real time

```powershell
# PowerShell — tail hub.log
Get-Content logs\hub.log -Wait

# Tail chat.log
Get-Content logs\chat.log -Wait

# Filter auth events only
Get-Content logs\hub.log -Wait | ForEach-Object {
    $_ | ConvertFrom-Json | Where-Object { $_.type -eq 'auth' }
}
```

---

## 9. Key Files

| File | Purpose |
|------|---------|
| `hub_service/auth.py` | Token mint, verify, RS256 key pair management |
| `hub_service/observability.py` | Structured log sink (stdout + hub.log + MySQL) |
| `hub_service/hub_server.py` | Hub API, admin UI, RBAC middleware |
| `chat_service/chat_server.py` | Chat UI, user auth, per-chat trace storage |
| `datalayer-as-service/mcp_server/auth.py` | MCP-side JWT verify + BearerClaimsMiddleware |
| `hub_service/.keys/private.pem` | RSA private key (hub signs tokens — never share) |
| `hub_service/.keys/public.pem` | RSA public key (matches JWKS) |
| `logs/hub.log` | Persistent hub event log (JSONL) |
| `logs/chat.log` | Persistent chat event log (JSONL) |

---

## 10. Troubleshooting

**401 on /discover from chat UI**
- Check `hub.log` for the `auth` event — look at `valid`, `iss`, `bearer_token`
- The hub accepts HS256 (iss=fab-chat) and RS256 (iss=fab-mcp-hub) — both are valid if signed with the correct key
- If `valid=false` and `token_type=unknown`, the token is malformed or uses a wrong secret

**Admin UI login button does nothing**
- Open DevTools → Console. Look for `[hub-admin] script loaded OK` — if missing, the HTML JS has a syntax error
- Look for `[hub-admin] doLogin called` — if missing, the submit handler is not firing
- After login POST, check `hub.log` for an `auth` event with `endpoint=/api/admin/login`

**MCP server rejects token (401)**
- Check MCP startup log for `auth=JWTVerifier (aud=<server_id>)` — if `aud=` is empty, `MCP_SERVER_ID` was not set
- The per-server token must have `aud` matching the server ID exactly
- Verify the JWKS endpoint: `curl http://localhost:8090/.well-known/jwks.json`

**Tokens look the same (all start with eyJhbGciOi)**
- This is normal — every JWT header is `{"alg":"HS256","typ":"JWT"}` or `{"alg":"RS256","typ":"JWT","kid":"hub-rsa-1"}`
- The header always encodes to the same base64 prefix; the payload and signature are always unique
