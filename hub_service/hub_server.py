"""
FAB MCP Hub Server — REST routing gateway for MCP server discovery.

Server registry is stored in MySQL fab_semantic.mcp_servers and cached
in-process for 60 seconds. Seed the table once with:

    python scripts/seed_hub_db.py

Endpoints:
    GET  /health            — service status + hub metadata  (public)
    GET  /.well-known/jwks.json — RSA public key for JWT validation (public)
    POST /auth/login        — username/password → RS256 JWT  (public)
    GET  /servers           — all registered server configs  (agent role required)
    GET  /servers/{id}      — single server config by ID     (agent role required)
    POST /discover          — {"intent": str} → servers + per-server tokens (agent role)
    GET  /api/logs          — recent observability events    (admin role required)

Routing strategy:
    LLM enabled  (HUB_LLM_ENABLED=true)  → ReAct agent reads server descriptions
                                            and picks the best match(es)
    LLM disabled (HUB_LLM_ENABLED=false) → returns first registered server

Authentication (hub_service/auth.py):
    AUTH_ENABLED     enable/disable token checks  (default: true)
    AUTH_PROVIDER    local | azure                (default: local)

    Open dev mode: AUTH_ENABLED=true but no keys configured → all pass (admin role)
    Local JWT:     set JWT_SECRET, mint token with:
                   python hub_service/auth.py --sub agent --roles agent --hours 24

RBAC roles:
    admin     — all endpoints including GET /api/logs
    agent     — POST /discover, GET /servers, GET /servers/{id}, GET /health
    readonly  — GET /servers, GET /servers/{id}, GET /health  (enforced)

Observability (hub_service/observability.py):
    Structured JSON events (auth, request, routing) emitted to stdout.
    GET /api/logs returns the in-memory ring buffer (last 500 events).
    Azure adaptation: replace print() in observability.py with Azure Monitor call.

Configuration (env vars):
    HUB_HOST         bind address          (default: 0.0.0.0)
    HUB_PORT         listen port           (default: 8090)
    HUB_LLM_ENABLED  use LLM routing       (default: true)
    OLLAMA_BASE_URL  Ollama endpoint       (default: http://localhost:11434/v1)
    OLLAMA_MODEL     model tag             (default: llama3.2:3b)
    MYSQL_*          DB credentials read from datalayer-as-service/.env
"""

import json
import os
import pathlib
import time

# Load root .env BEFORE importing auth — hub_service/auth.py reads HUB_API_KEY
# and JWT_SECRET at module level, so they must be in os.environ first.
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

import httpx
import uvicorn
from fastapi import Depends, FastAPI, Form, HTTPException, Request, Security
from fastapi.responses import RedirectResponse
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
import warnings as _warnings
# Suppress LangGraph v1.x deprecation: fires at call site, not import time.
# Migration to langchain.agents not applicable — langchain package not installed.
_warnings.filterwarnings("ignore", message="create_react_agent")
# Suppress FastAPI on_event deprecation — refactor to lifespan deferred.
_warnings.filterwarnings("ignore", message="on_event is deprecated")
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware

from auth import AUTH_ENABLED, AUTH_PROVIDER, generate_server_token, get_jwks, verify_token
from db import get_engine  # hub_service/db.py
from observability import get_events, log_event

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

HUB_HOST        = os.environ.get("HUB_HOST",        "0.0.0.0")
HUB_PORT        = int(os.environ.get("HUB_PORT",    "8090"))
HUB_LLM_ENABLED = os.environ.get("HUB_LLM_ENABLED", "true").lower() in ("1", "true", "yes")
OLLAMA_URL      = os.environ.get("OLLAMA_BASE_URL",  "http://localhost:11434/v1")
MODEL           = os.environ.get("OLLAMA_MODEL",     "llama3.2:3b")

# Simple in-process cache — avoids a DB round-trip on every /discover call
_HUB_CACHE_TTL  = 60  # seconds
_hub_cache: dict | None = None
_hub_cache_at: float    = 0.0

_lc_llm: ChatOpenAI | None = None

HUB_ADMIN_USERNAME = os.environ.get("HUB_ADMIN_USERNAME", "admin")
HUB_ADMIN_PASSWORD = os.environ.get("HUB_ADMIN_PASSWORD", "admin")
MCP_API_KEY        = os.environ.get("MCP_API_KEY",        "")


# ---------------------------------------------------------------------------
# Request logging middleware
# ---------------------------------------------------------------------------

class _RequestLogMiddleware(BaseHTTPMiddleware):
    """Log every HTTP request as a structured observability event.

    Uses finally so the log entry is emitted even when the route handler
    raises an exception (e.g. SQLAlchemy OperationalError when MySQL is down).
    """

    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            log_event(
                "request",
                method=request.method,
                path=request.url.path,
                status=status_code,
                latency_ms=round((time.monotonic() - start) * 1000),
            )


app = FastAPI(title="FAB MCP Hub Server", version="2.0.0")
app.add_middleware(_RequestLogMiddleware)


@app.on_event("startup")
async def _startup():
    """Create changelog table and ensure api_key columns exist (auto-migration)."""
    try:
        with get_engine().begin() as conn:
            conn.execute(text(
                "CREATE TABLE IF NOT EXISTS mcp_server_changelog ("
                "id BIGINT AUTO_INCREMENT PRIMARY KEY, "
                "server_id VARCHAR(128), "
                "action VARCHAR(32), "
                "changed_by VARCHAR(64), "
                "changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
                "before_state JSON, "
                "after_state JSON"
                ")"
            ))
            # Auto-migrate: add api_key and api_key_expires columns if missing,
            # then generate unique keys for any server that has none.
            try:
                import secrets as _sec
                existing_cols = {
                    row[0] for row in conn.execute(text(
                        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mcp_servers'"
                    ))
                }
                if "api_key" not in existing_cols:
                    conn.execute(text(
                        "ALTER TABLE mcp_servers ADD COLUMN api_key VARCHAR(1000) DEFAULT NULL "
                        "COMMENT 'Per-server Bearer token. Overrides MCP_API_KEY env var.'"
                    ))
                if "api_key_expires" not in existing_cols:
                    conn.execute(text(
                        "ALTER TABLE mcp_servers ADD COLUMN api_key_expires TIMESTAMP DEFAULT NULL"
                    ))
                # Generate unique per-server keys for servers with no key yet.
                # Servers already having any key are left unchanged — use the
                # "Rotate Keys" button in Admin UI to explicitly replace them.
                # Find servers with no key, an empty key, or a legacy 'mcp-' prefixed key.
                # 'mcp-' was the prefix used by older auto-generation code; any key with that
                # prefix is treated as absent so a fresh 'srv-' key replaces it automatically.
                # Servers already carrying a 'srv-' key or a manually set JWT are left unchanged.
                _need_key = conn.execute(text(
                    "SELECT id FROM mcp_servers "
                    "WHERE api_key IS NULL OR api_key = '' OR api_key LIKE 'mcp-%'"
                )).fetchall()
                for _row in _need_key:
                    conn.execute(text(
                        "UPDATE mcp_servers SET api_key=:key WHERE id=:id"
                    ), {"key": "srv-" + _sec.token_hex(32), "id": _row[0]})
                if _need_key:
                    print(f"[startup] Generated unique API keys for {len(_need_key)} server(s)")
            except Exception:
                pass  # mcp_servers table may not exist yet (first run before seed)
    except Exception as _e:
        print(f"[startup] DB migration skipped (MySQL unavailable or error): {_e}")


# ---------------------------------------------------------------------------
# Auth dependencies
# ---------------------------------------------------------------------------

_bearer = HTTPBearer(auto_error=False)


def _require_auth(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> dict:
    """Validate Bearer token, log the auth event, return claims dict."""
    token = creds.credentials if creds else ""
    valid, claims = verify_token(token)
    log_event(
        "auth",
        valid=valid,
        sub=claims.get("sub", "unknown"),
        roles=claims.get("roles", []),
        token_type=_classify_token(claims) if valid else "unknown",
        iss=claims.get("iss"),
        provider=AUTH_PROVIDER if AUTH_ENABLED else "disabled",
        endpoint=request.url.path,
        method=request.method,
        bearer_token=token or None,
    )
    if not valid:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return claims


def _require_agent(claims: dict = Depends(_require_auth)) -> dict:
    """Enforce 'agent' or 'admin' role."""
    roles = claims.get("roles", [])
    if "agent" not in roles and "admin" not in roles:
        raise HTTPException(status_code=403, detail="Role 'agent' required")
    return claims


def _require_admin(claims: dict = Depends(_require_auth)) -> dict:
    """Enforce 'admin' role."""
    if "admin" not in claims.get("roles", []):
        raise HTTPException(status_code=403, detail="Role 'admin' required")
    return claims


def _require_readonly(claims: dict = Depends(_require_auth)) -> dict:
    """Enforce 'readonly', 'agent', or 'admin' role — read-only access."""
    roles = claims.get("roles", [])
    if not any(r in roles for r in ("readonly", "agent", "admin")):
        raise HTTPException(status_code=403, detail="At least 'readonly' role required")
    return claims


def _classify_token(claims: dict) -> str:
    """Determine token type from internal markers set by auth.verify_token().

    auth.py tags each validated claims dict with a '_source' key and a
    specific 'sub' value depending on what was presented:
      jwt      → RS256 or HS256 JWT successfully decoded and verified
      apikey   → HUB_API_KEY static pre-shared key match
      dev      → open-dev mode (no keys configured, all requests pass)
    This label is logged with every auth event so operators can distinguish
    JWT-authenticated calls from static-key or dev-mode calls at a glance.
    """
    if claims.get("_source") == "jwt":
        return "jwt"
    if claims.get("sub") == "api-key-user":
        return "apikey"
    if claims.get("sub") in ("dev", "anonymous"):
        return "dev"
    return "jwt"  # Azure AD and other external JWT providers also land here


# ---------------------------------------------------------------------------
# Hub registry — loaded from MySQL fab_semantic.mcp_servers
# ---------------------------------------------------------------------------

def load_hub() -> dict:
    """Return hub registry from MySQL, using a 60-second in-process cache."""
    global _hub_cache, _hub_cache_at
    now = time.monotonic()
    if _hub_cache is not None and (now - _hub_cache_at) < _HUB_CACHE_TTL:
        return _hub_cache

    with get_engine().connect() as conn:
        try:
            rows = conn.execute(
                text(
                    "SELECT id, name, endpoint, transport, capability, skills, description, "
                    "examples, start_cmd, api_key "
                    "FROM mcp_servers WHERE is_active = 1 ORDER BY id"
                )
            ).fetchall()
            _has_api_key_col = True
        except Exception:
            # api_key column not yet added — run: python scripts/seed_hub_db.py
            rows = conn.execute(
                text(
                    "SELECT id, name, endpoint, transport, capability, skills, description, "
                    "examples, start_cmd "
                    "FROM mcp_servers WHERE is_active = 1 ORDER BY id"
                )
            ).fetchall()
            _has_api_key_col = False

    servers = []
    for row in rows:
        def _parse_json(val):
            if isinstance(val, str):
                try:
                    return json.loads(val)
                except (ValueError, TypeError):
                    return []
            return val or []

        api_key = (row._mapping.get("api_key") or "") if _has_api_key_col else ""
        servers.append({
            "id":          row.id,
            "name":        row.name,
            "endpoint":    row.endpoint,
            "transport":   row.transport,
            "capability":  row.capability or "",
            "skills":      _parse_json(row.skills),
            "description": row.description or "",
            "examples":    _parse_json(row.examples),
            "start_cmd":   row.start_cmd or "",
            "api_key":     api_key,  # per-server credential; agent uses this over MCP_API_KEY env var
        })

    _hub_cache    = {"hub_name": "FAB MCP Hub", "version": "3.0", "servers": servers}
    _hub_cache_at = now
    return _hub_cache


# ---------------------------------------------------------------------------
# Routing tools — per-request factory to isolate concurrent request state
# ---------------------------------------------------------------------------

_ROUTING_PROMPT = (
    "You are a routing agent for the FAB MCP Hub.\n"
    "You will be given a list of servers and a user query.\n"
    "Use Capability and Skills to match the query domain.\n\n"
    "Rules:\n"
    "- Focused query (one domain): call pick_server() once with the best server.\n"
    "- Comprehensive query explicitly needing multiple domains: call pick_server() "
    "once for each relevant server.\n"
    "Use exact server IDs from the list. Do not invent server IDs."
)


def _make_routing_tools(decision: dict) -> list:
    """Return a fresh pick_server tool that writes selections into the caller's dict.

    A new tool instance is created per request so that concurrent /discover calls
    each accumulate their own decisions without sharing state. Using a module-level
    tool or a class instance would cause race conditions where two simultaneous
    requests overwrite each other's server_ids list.
    """

    @tool
    def pick_server(server_id: str, reason: str) -> str:
        """Select a server for this query. Call once per server needed.

        Args:
            server_id: Exact ID from the server list (e.g. 'deal-service').
            reason: One sentence — which capability/skill matched and why.
        """
        ids = decision.setdefault("server_ids", [])
        if server_id and server_id not in ids:
            ids.append(server_id)
        decision["reason"] = reason
        return f"Added '{server_id}' (selected: {len(ids)})"

    return [pick_server]


def _keyword_route(servers: list[dict], intent: str) -> tuple[list[str], str]:
    """Deterministic keyword fallback when the LLM router is unavailable.

    Scoring (additive):
      • base  — 1 pt per 4+ char intent word found anywhere in the server's
                 id, capability, description, skills, or examples corpus
      • bonus — +1 pt per intent word that also appears in the server's ID tokens
                 (the server ID is the most authoritative domain signal; a word
                 like "pricing" in "fab-pricing-server" should outweigh the same
                 word buried in another server's description)

    Returns the highest-scoring server's ID or [] (caller falls through to the
    first-server default).

    Examples with default server registry:
        "analyze pricing for CUST001" → fab-pricing-server  ("pricing" in ID +2, corpus +1)
        "360 profile for CUST001"     → fab-customer-server ("profile" + "cust001" in corpus)
        "calculate factorial of 15"   → calculator-server   ("calculate" in ID)
        "weather in Dubai"            → weather-server      ("weather" in ID)
        "currency in Japan"           → data-server         ("currency" in description)
    """
    q_words = {w for w in intent.lower().split() if len(w) >= 4}
    if not q_words:
        return [], "keyword: query too short"

    best_id    = ""
    best_score = 0
    for server in servers:
        sid      = server["id"]
        id_words = set(sid.replace("-", " ").lower().split())

        # Full corpus: id + all metadata fields (lowercase for matching)
        corpus = " ".join([
            sid.replace("-", " "),
            server.get("capability", ""),
            server.get("description", ""),
            " ".join(server.get("skills", [])),
            " ".join(server.get("examples", [])),
        ]).lower()

        base  = sum(1 for w in q_words if w in corpus)
        bonus = sum(1 for w in q_words if w in id_words)   # extra weight for ID match
        score = base + bonus

        if score > best_score:
            best_score = score
            best_id    = sid

    if not best_id:
        return [], "keyword: no match"
    return [best_id], f"keyword match (score={best_score})"


def _build_server_context(servers: list[dict]) -> str:
    """Format server list for inline injection into the routing message."""
    parts = []
    for s in servers:
        skills   = ", ".join(s.get("skills", []))
        examples = " | ".join(f'"{e}"' for e in s.get("examples", [])[:2])
        parts.append(
            f'Server ID   : {s["id"]}\n'
            f'Capability  : {s.get("capability", "")}\n'
            f'Description : {s.get("description", "")}\n'
            f'Skills      : {skills}\n'
            f'Examples    : {examples}'
        )
    return "\n\n".join(parts)


def _get_llm() -> ChatOpenAI:
    """Return the shared ChatOpenAI instance (cached singleton)."""
    global _lc_llm
    if _lc_llm is None:
        _lc_llm = ChatOpenAI(
            base_url=OLLAMA_URL, openai_api_key="ollama", model=MODEL,
            temperature=0, request_timeout=90,
        )
    return _lc_llm


async def _agent_route(servers: list[dict], intent: str) -> tuple[list[str], str]:
    """Run the routing agent and return (server_ids, reason) with full trace logging."""
    decision: dict = {}
    tools = _make_routing_tools(decision)
    agent = create_react_agent(_get_llm(), tools, prompt=_ROUTING_PROMPT)

    server_context = _build_server_context(servers)
    message = (
        f"Available servers:\n\n{server_context}\n\n"
        f"Query: {intent}\n\n"
        f"Call pick_server() for each server that should handle this query "
        f"(usually just one, unless the query explicitly spans multiple domains)."
    )

    print("[hub]  ── LLM routing ──────────────────────────────")
    print(f"[hub]  query  : {intent}")
    print(f"[hub]  servers: {[s['id'] for s in servers]}")

    async for event in agent.astream_events(
        {"messages": [HumanMessage(content=message)]},
        version="v2",
    ):
        kind = event["event"]

        if kind == "on_tool_start" and event["name"] == "pick_server":
            args = event["data"].get("input", {})
            print("[hub]  agent  → pick_server(")
            print(f"[hub]              server_id = {args.get('server_id')!r}")
            print(f"[hub]              reason    = {args.get('reason')!r}")
            print("[hub]          )")

        elif kind == "on_tool_end" and event["name"] == "pick_server":
            output = event["data"].get("output")
            result = output.content if hasattr(output, "content") else str(output)
            print(f"[hub]  agent  ← {result[:120]}")

        elif kind == "on_chat_model_end":
            output = event["data"].get("output")
            if output is None:
                continue
            raw = getattr(output, "content", None)
            if raw and not getattr(output, "tool_calls", []):
                print(f"[hub]  agent reasoning: {raw[:300]}")

    server_ids = decision.get("server_ids", [])
    reason     = decision.get("reason", "agent routing")
    print(f"[hub]  selected : {server_ids!r}")
    print(f"[hub]  reason   : {reason}")
    print("[hub]  ───────────────────────────────────────────────")
    return server_ids, reason


# ---------------------------------------------------------------------------
# Main routing dispatcher
# ---------------------------------------------------------------------------

async def route_to_server(hub: dict, intent: str) -> tuple[list[dict], str, str]:
    """Route intent → (servers, method, reason).

    Returns a list of matched server dicts (1 for focused queries, N for
    multi-domain queries). LLM enabled → ReAct agent picks the best server(s).
    LLM disabled → returns the first registered server.
    """
    servers      = hub["servers"]
    server_by_id = {s["id"]: s for s in servers}

    if HUB_LLM_ENABLED:
        try:
            server_ids, reason = await _agent_route(servers, intent)
        except Exception as e:
            print(f"[hub]  WARNING: agent routing failed ({e}) — keyword fallback")
            server_ids, reason = [], "agent routing failed"

        matched = [server_by_id[sid] for sid in server_ids if sid in server_by_id]
        if matched:
            method = "agent"
            log_event(
                "routing",
                method=method,
                server_ids=[s["id"] for s in matched],
                reason=reason,
                intent=intent[:120],
            )
            return matched, method, reason

        # ── LLM failed or returned unknown IDs — keyword fallback ────────────
        kw_ids, kw_reason = _keyword_route(servers, intent)
        kw_matched = [server_by_id[sid] for sid in kw_ids if sid in server_by_id]
        if kw_matched:
            method = "keyword"
            print(f"[hub]  keyword  : {kw_ids!r} — {kw_reason}")
            log_event("routing", method=method, server_ids=[s["id"] for s in kw_matched],
                      reason=kw_reason, intent=intent[:120])
            return kw_matched, method, kw_reason

        print(f"[hub]  WARNING: {server_ids!r} no keyword match — defaulting to first server")
        if servers:
            log_event("routing", method="agent", server_ids=[servers[0]["id"]], reason="defaulted to first server", intent=intent[:120])
            return [servers[0]], "agent", "defaulted to first server"
        log_event("routing", method="agent", server_ids=[], reason="no servers available", intent=intent[:120])
        return [], "agent", "no servers available"

    # LLM disabled — return first registered server
    print("[hub]  mode   : LLM disabled — returning first registered server")
    if servers:
        log_event("routing", method="first_match", server_ids=[servers[0]["id"]], reason="LLM routing disabled", intent=intent[:120])
        return [servers[0]], "first_match", "LLM routing disabled — returning first registered server"
    log_event("routing", method="first_match", server_ids=[], reason="no servers available", intent=intent[:120])
    return [], "first_match", "no servers available"


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class DiscoverRequest(BaseModel):
    intent: str


class ServerCreateRequest(BaseModel):
    id: str
    name: str
    endpoint: str
    transport: str = "sse"
    capability: str = ""
    skills: list[str] = []
    description: str = ""
    examples: list[str] = []
    start_cmd: str = ""
    is_active: bool = True


class ServerUpdateRequest(BaseModel):
    name: str | None = None
    endpoint: str | None = None
    transport: str | None = None
    capability: str | None = None
    skills: list[str] | None = None
    description: str | None = None
    examples: list[str] | None = None
    start_cmd: str | None = None
    is_active: bool | None = None


class TokenCreateRequest(BaseModel):
    sub: str
    roles: list[str] = ["agent"]
    hours: int = 24
    audience: str | None = None
    server_id: str | None = None


class HubLoginRequest(BaseModel):
    username: str
    password: str


def _invalidate_hub_cache() -> None:
    """Force the next load_hub() call to re-read from MySQL."""
    global _hub_cache, _hub_cache_at
    _hub_cache = None
    _hub_cache_at = 0.0


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():  # public — no auth required
    hub = load_hub()
    return {
        "status":       "ok",
        "hub_name":     hub.get("hub_name", "FAB MCP Hub"),
        "version":      hub.get("version", "unknown"),
        "llm_enabled":  HUB_LLM_ENABLED,
        "server_count": len(hub.get("servers", [])),
        "server_ids":   [s["id"] for s in hub.get("servers", [])],
    }


@app.get("/.well-known/jwks.json")
async def jwks():
    return get_jwks()


@app.post("/auth/login")
async def auth_login(req: HubLoginRequest):
    """Public login for agents — returns an RS256 JWT for hub and MCP access.

    The returned token is accepted by:
      • POST /discover (agent role)
      • Each MCP server's JWTVerifier (audience = server_id)

    Credentials:
      Admin  : HUB_ADMIN_USERNAME / HUB_ADMIN_PASSWORD  → roles: ['admin']
      Agent  : HUB_AGENT_USERNAME / HUB_AGENT_PASSWORD  → roles: ['agent']
    """
    agent_username = os.environ.get("HUB_AGENT_USERNAME", "agent")
    agent_password = os.environ.get("HUB_AGENT_PASSWORD", "")

    if req.username == HUB_ADMIN_USERNAME and req.password == HUB_ADMIN_PASSWORD:
        roles = ["admin"]
    elif agent_password and req.username == agent_username and req.password == agent_password:
        roles = ["agent"]
    else:
        log_event("auth", valid=False, sub=req.username, roles=[], token_type="password",
                  endpoint="/auth/login", method="POST", provider="local")
        raise HTTPException(status_code=401, detail="Invalid credentials")

    from auth import generate_token as _gen
    token = _gen(sub=req.username, roles=roles, expires_hours=8)
    log_event("auth", valid=True, sub=req.username, roles=roles, token_type="password",
              endpoint="/auth/login", method="POST", provider="local")
    return {"access_token": token, "token_type": "bearer", "sub": req.username, "roles": roles}


@app.get("/servers", dependencies=[Depends(_require_readonly)])
async def list_servers():
    hub = load_hub()
    return {"servers": hub["servers"]}


@app.get("/servers/all", dependencies=[Depends(_require_admin)])
async def list_all_servers():
    """Return all servers including inactive ones — admin-only view for the UI Servers tab.

    Includes the full api_key value (not just the hint) so the Admin UI can
    display and copy it. The two-SELECT fallback silently degrades when the
    api_key column has not yet been added via `python scripts/seed_hub_db.py`.
    Run the seed script once to add the column; after that _has_ak is always True.
    """
    with get_engine().connect() as conn:
        try:
            rows = conn.execute(
                text("SELECT id,name,endpoint,transport,capability,skills,description,"
                     "examples,start_cmd,is_active,api_key FROM mcp_servers ORDER BY id")
            ).fetchall()
            _has_ak = True
        except Exception:
            # api_key column absent (pre-migration schema) — fall back to query
            # without it. Admin UI will show all keys as 'not set' in this state.
            rows = conn.execute(
                text("SELECT id,name,endpoint,transport,capability,skills,description,"
                     "examples,start_cmd,is_active FROM mcp_servers ORDER BY id")
            ).fetchall()
            _has_ak = False
    def _pj(v):
        if isinstance(v, str):
            try: return json.loads(v)
            except: return []
        return v or []
    result = []
    for r in rows:
        ak = (r._mapping.get("api_key") or "") if _has_ak else ""
        result.append({
            "id": r.id, "name": r.name, "endpoint": r.endpoint, "transport": r.transport,
            "capability": r.capability or "", "skills": _pj(r.skills),
            "description": r.description or "", "examples": _pj(r.examples),
            "start_cmd": r.start_cmd or "", "is_active": bool(r.is_active),
            "api_key": ak,
            "api_key_set": bool(ak),
            "api_key_hint": (ak[:8] + "...") if len(ak) > 8 else ("not set" if not ak else ak),
        })
    return {"servers": result}


@app.get("/servers/{server_id}", dependencies=[Depends(_require_readonly)])
async def get_server(server_id: str):
    hub   = load_hub()
    by_id = {s["id"]: s for s in hub["servers"]}
    if server_id not in by_id:
        raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found in hub")
    return by_id[server_id]


@app.post("/discover")
async def discover(request: DiscoverRequest, claims: dict = Depends(_require_agent)):
    """Route an intent to the best-matching MCP server(s).

    Returns auth_meta so the caller can confirm which identity was validated
    and what token type was used — this feeds the UI security trace tab.
    """
    hub                     = load_hub()
    servers, method, reason = await route_to_server(hub, request.intent)

    # Mint a short-lived per-server JWT for each matched server.
    # Each token is audience-scoped (aud = server_id) so FastMCP's JWTVerifier
    # on the target server can prove the token was issued for it specifically.
    # A token for server A is cryptographically rejected by server B even though
    # both share the same hub JWKS endpoint.
    #
    # expires_hours=1 — deliberately short. The agent uses the token immediately
    # for a single query session (seconds to minutes). A 1-hour window covers
    # slow LLM turns and retries without keeping a long-lived credential alive.
    # User session JWTs (from /auth/login) are 8 hours; these per-server tokens
    # are intentionally shorter because they are created per-query, not per-login.
    from auth import generate_server_token as _gen_srv
    for s in servers:
        s["server_token"] = _gen_srv(
            server_id=s["id"],
            sub=claims.get("sub", "agent"),       # forward the caller's identity into the token
            roles=claims.get("roles", ["agent"]), # forward the caller's roles for MCP-side RBAC
            expires_hours=1,
        )

    log_event(
        "routing",
        sub=claims.get("sub"),
        method=method,
        reason=reason,
        server_ids=[s["id"] for s in servers],
        intent=request.intent,
    )
    log_event(
        "request_detail",
        endpoint="/discover",
        method="POST",
        request_headers={
            "Authorization": f"Bearer <{claims.get('token_type', 'jwt').upper()}> sub={claims.get('sub', '?')}",
            "Content-Type": "application/json",
        },
        request_body={"intent": request.intent},
        response_status=200,
        response_headers={"Content-Type": "application/json"},
        response_body={
            "servers": [{"id": s["id"], "endpoint": s["endpoint"], "transport": s.get("transport")} for s in servers],
            "method": method,
            "reason": reason[:200] if reason else "",
        },
        auth_sub=claims.get("sub"),
        auth_roles=claims.get("roles", []),
    )
    return {
        "servers": servers,
        "method":  method,
        "reason":  reason,
        "hub_metadata": {
            "hub_name":     hub.get("hub_name", "FAB MCP Hub"),
            "version":      hub.get("version", "unknown"),
            "server_count": len(hub["servers"]),
            "server_ids":   [s["id"] for s in hub["servers"]],
        },
        "auth_meta": {
            "sub":        claims.get("sub"),
            "roles":      claims.get("roles", []),
            "token_type": _classify_token(claims),
            "iss":        claims.get("iss"),
            "exp":        claims.get("exp"),
        },
    }


@app.get("/api/logs", dependencies=[Depends(_require_admin)])
async def api_logs(n: int = 100, event_type: str = ""):
    """Return recent structured observability events from the in-memory buffer.

    Query params:
        n           max events to return (1–500, default 100)
        event_type  filter by type: auth, request, routing, error (default: all)
    """
    events = get_events(n=min(max(n, 1), 500), event_type=event_type or None)
    return {"events": events, "returned": len(events)}


@app.get("/api/logs/file", dependencies=[Depends(_require_admin)])
async def api_logs_file(n: int = 200):
    """Return the last *n* lines of the hub.log file (JSONL, newest-last).

    The log file is written to logs/hub.log in the project root and persists
    across process restarts. Useful for post-mortem tracing.
    """
    import pathlib as _pl
    log_path = _pl.Path(__file__).resolve().parent.parent / "logs" / "hub.log"
    if not log_path.exists():
        return {"lines": [], "file": str(log_path), "note": "log file not yet created"}
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
        tail = lines[-min(n, len(lines)):]
        parsed = []
        for line in tail:
            try:
                import json as _json
                parsed.append(_json.loads(line))
            except Exception:
                parsed.append({"_raw": line})
        return {"lines": parsed, "returned": len(parsed), "file": str(log_path)}
    except Exception as exc:
        return {"error": str(exc), "file": str(log_path)}


# ---------------------------------------------------------------------------
# Admin UI + MCP server CRUD + Token API
# ---------------------------------------------------------------------------

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, login_error: str = ""):
    """Hub Admin Console — serves the admin UI.
    Login can be done via the form (JS-free form POST) or the JS fetch path.
    A hub_admin_session cookie is set on form login and read back here so the
    page can skip the login form entirely.
    """
    cookie_tok  = request.cookies.get("hub_admin_session", "")
    err_html    = ""
    if login_error == "1":
        err_html = "Invalid username or password"
    elif login_error == "2":
        err_html = "Server error — check hub logs"
    page = _ADMIN_HTML.replace("HUB_COOKIE_TOKEN", cookie_tok)
    if err_html:
        page = page.replace(
            '<div id="le2" class="err-msg"></div>',
            f'<div id="le2" class="err-msg">{err_html}</div>',
        )
    return HTMLResponse(page, headers={"Cache-Control": "no-store, max-age=0"})


@app.post("/admin/login")
async def admin_form_login(
    username: str = Form(default=""),
    password: str = Form(default=""),
):
    """Form-POST login — sets hub_admin_session cookie and redirects to /admin.
    Works even when the browser blocks JavaScript.
    """
    if username != HUB_ADMIN_USERNAME or password != HUB_ADMIN_PASSWORD:
        log_event("auth", valid=False, sub=username, roles=[], token_type="password",
                  endpoint="/admin/login", method="POST", provider="local")
        return RedirectResponse("/admin?login_error=1", status_code=303)
    try:
        from auth import generate_token as _gen, _DEV_MODE_ACTIVE as _dm
        import secrets as _s
        token = ("dev-admin-" + _s.token_hex(20)) if _dm else _gen(
            sub=username, roles=["admin"], expires_hours=8
        )
    except Exception:
        return RedirectResponse("/admin?login_error=2", status_code=303)
    log_event("auth", valid=True, sub=username, roles=["admin"], token_type="password",
              endpoint="/admin/login", method="POST", provider="local")
    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie("hub_admin_session", token,
                    httponly=False, samesite="lax", max_age=8 * 3600, path="/")
    return resp


@app.post("/servers")
async def create_server(req: ServerCreateRequest, claims: dict = Depends(_require_admin)):
    """Register a new MCP server in the hub registry."""
    with get_engine().begin() as conn:
        if conn.execute(text("SELECT id FROM mcp_servers WHERE id = :id"), {"id": req.id}).fetchone():
            raise HTTPException(status_code=409, detail=f"Server '{req.id}' already exists")
        conn.execute(
            text(
                "INSERT INTO mcp_servers "
                "(id, name, endpoint, transport, capability, skills, description, examples, start_cmd, is_active) "
                "VALUES (:id,:name,:endpoint,:transport,:capability,:skills,:description,:examples,:start_cmd,:is_active)"
            ),
            {
                "id": req.id, "name": req.name, "endpoint": req.endpoint,
                "transport": req.transport, "capability": req.capability,
                "skills": json.dumps(req.skills), "description": req.description,
                "examples": json.dumps(req.examples), "start_cmd": req.start_cmd,
                "is_active": 1 if req.is_active else 0,
            },
        )
        conn.execute(
            text(
                "INSERT INTO mcp_server_changelog "
                "(server_id, action, changed_by, before_state, after_state) "
                "VALUES (:server_id, :action, :changed_by, :before_state, :after_state)"
            ),
            {
                "server_id": req.id,
                "action": "create",
                "changed_by": claims.get("sub", "unknown"),
                "before_state": None,
                "after_state": json.dumps(req.model_dump()),
            },
        )
    _invalidate_hub_cache()
    log_event("admin", action="create_server", server_id=req.id, changed_by=claims.get("sub"))
    return {"ok": True, "id": req.id}


@app.put("/servers/{server_id}")
async def update_server(server_id: str, req: ServerUpdateRequest, claims: dict = Depends(_require_admin)):
    """Update an MCP server's configuration."""
    updates = req.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")
    for field in ("skills", "examples"):
        if field in updates and isinstance(updates[field], list):
            updates[field] = json.dumps(updates[field])
    if "is_active" in updates:
        updates["is_active"] = 1 if updates["is_active"] else 0
    set_clause = ", ".join(f"{k}=:{k}" for k in updates)
    updates["_id"] = server_id
    with get_engine().begin() as conn:
        before_row = conn.execute(
            text(
                "SELECT id,name,endpoint,transport,capability,skills,description,examples,start_cmd,is_active "
                "FROM mcp_servers WHERE id=:id"
            ),
            {"id": server_id},
        ).fetchone()
        result = conn.execute(text(f"UPDATE mcp_servers SET {set_clause} WHERE id=:_id"), updates)
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")
        before_state = dict(before_row._mapping) if before_row else None
        conn.execute(
            text(
                "INSERT INTO mcp_server_changelog "
                "(server_id, action, changed_by, before_state, after_state) "
                "VALUES (:server_id, :action, :changed_by, :before_state, :after_state)"
            ),
            {
                "server_id": server_id,
                "action": "update",
                "changed_by": claims.get("sub", "unknown"),
                "before_state": json.dumps(before_state, default=str) if before_state else None,
                "after_state": json.dumps(req.model_dump(exclude_none=True)),
            },
        )
    _invalidate_hub_cache()
    log_event("admin", action="update_server", server_id=server_id, changed_by=claims.get("sub"))
    return {"ok": True, "id": server_id}


@app.delete("/servers/{server_id}")
async def delete_server(server_id: str, claims: dict = Depends(_require_admin)):
    """Remove an MCP server from the hub registry."""
    with get_engine().begin() as conn:
        before_row = conn.execute(
            text(
                "SELECT id,name,endpoint,transport,capability,skills,description,examples,start_cmd,is_active "
                "FROM mcp_servers WHERE id=:id"
            ),
            {"id": server_id},
        ).fetchone()
        result = conn.execute(text("DELETE FROM mcp_servers WHERE id=:id"), {"id": server_id})
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")
        before_state = dict(before_row._mapping) if before_row else None
        conn.execute(
            text(
                "INSERT INTO mcp_server_changelog "
                "(server_id, action, changed_by, before_state, after_state) "
                "VALUES (:server_id, :action, :changed_by, :before_state, :after_state)"
            ),
            {
                "server_id": server_id,
                "action": "delete",
                "changed_by": claims.get("sub", "unknown"),
                "before_state": json.dumps(before_state, default=str) if before_state else None,
                "after_state": None,
            },
        )
    _invalidate_hub_cache()
    log_event("admin", action="delete_server", server_id=server_id, changed_by=claims.get("sub"))
    return {"ok": True}


@app.get("/api/servers/changelog", dependencies=[Depends(_require_admin)])
async def get_server_changelog(server_id: str = "", limit: int = 50):
    """Return MCP server change log entries (admin only).

    Query params:
        server_id  filter by server (default: all)
        limit      max rows to return (1–200, default 50)
    """
    limit = min(max(limit, 1), 200)
    params: dict = {"limit": limit}
    if server_id:
        q = (
            "SELECT id, server_id, action, changed_by, changed_at, before_state, after_state "
            "FROM mcp_server_changelog WHERE server_id = :server_id "
            "ORDER BY changed_at DESC LIMIT :limit"
        )
        params["server_id"] = server_id
    else:
        q = (
            "SELECT id, server_id, action, changed_by, changed_at, before_state, after_state "
            "FROM mcp_server_changelog ORDER BY changed_at DESC LIMIT :limit"
        )
    with get_engine().connect() as conn:
        rows = conn.execute(text(q), params).fetchall()

    def _parse(v):
        if v is None:
            return None
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return v
        return v

    return {
        "changelog": [
            {
                "id": r.id,
                "server_id": r.server_id,
                "action": r.action,
                "changed_by": r.changed_by,
                "changed_at": r.changed_at.isoformat() if hasattr(r.changed_at, "isoformat") else str(r.changed_at),
                "before_state": _parse(r.before_state),
                "after_state": _parse(r.after_state),
            }
            for r in rows
        ]
    }


def _build_curl(method: str, url: str, headers: dict, body) -> str:
    """Build a curl command string."""
    import json as _json
    parts = [f"curl -X {method} '{url}'"]
    for k, v in headers.items():
        parts.append(f"  -H '{k}: {v}'")
    if body:
        parts.append(f"  -d '{_json.dumps(body)}'")
    return " \\\n".join(parts)


def _build_http_raw(method: str, url: str, headers: dict, body) -> str:
    """Build a raw HTTP/1.1 request string."""
    import json as _json
    from urllib.parse import urlparse
    p = urlparse(url)
    path = (p.path or "/") + (("?" + p.query) if p.query else "")
    lines = [f"{method} {path} HTTP/1.1", f"Host: {p.netloc}"]
    body_str = _json.dumps(body) if body else ""
    for k, v in headers.items():
        lines.append(f"{k}: {v}")
    if body_str:
        lines.append(f"Content-Length: {len(body_str.encode())}")
    lines.append("")
    if body_str:
        lines.append(body_str)
    return "\n".join(lines)


def _make_request_detail(method: str, url: str, headers: dict, body) -> dict:
    """Return request detail dict with curl, http_raw, and headers_json formats."""
    import json as _json
    return {
        "method": method,
        "url": url,
        "headers": dict(headers),
        "body": body,
        "curl": _build_curl(method, url, headers, body),
        "http_raw": _build_http_raw(method, url, headers, body),
        "headers_json": _json.dumps(dict(headers), indent=2),
        "body_json": _json.dumps(body, indent=2) if body else None,
    }


# FastMCP streamable-http requires both content types in Accept header
_MCP_ACCEPT = "application/json, text/event-stream"


def _parse_mcp_body(r: httpx.Response) -> dict:
    """Parse an MCP response that may be JSON or SSE event-stream."""
    ct = r.headers.get("content-type", "")
    if "text/event-stream" in ct:
        for line in r.text.splitlines():
            if line.startswith("data: ") and line != "data: ":
                try:
                    return json.loads(line[6:])
                except Exception:
                    pass
        return {}
    return r.json()


async def _mcp_json_rpc(
    endpoint: str, body: dict, headers: dict, timeout: float = 6.0
) -> tuple[dict, str | None]:
    """
    Perform a streamable-HTTP JSON-RPC call: initialize → get session-id → call.

    FastMCP is stateful: every connection starts with `initialize` which returns
    an `Mcp-Session-Id` header.  All subsequent requests on that connection must
    include that header or the server returns 400 "Missing session ID".

    FastMCP also requires Accept to include BOTH application/json AND
    text/event-stream; sending only application/json returns 406.
    """
    _hdrs = {**headers, "Accept": _MCP_ACCEPT}
    _init_body = {
        "jsonrpc": "2.0", "id": "probe-init", "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "hub-admin-probe", "version": "1.0"},
        },
    }
    # Strip trailing slash — FastMCP redirects /mcp/ → /mcp (307) which loses POST body
    endpoint = endpoint.rstrip("/")
    async with httpx.AsyncClient(follow_redirects=True) as client:
        r_init = await client.post(endpoint, json=_init_body, headers=_hdrs, timeout=timeout)
        r_init.raise_for_status()
        session_id = (r_init.headers.get("mcp-session-id")
                      or r_init.headers.get("Mcp-Session-Id") or "")
        _hdrs_s = {**_hdrs, **({"mcp-session-id": session_id} if session_id else {})}
        r = await client.post(endpoint, json=body, headers=_hdrs_s, timeout=timeout)
        r.raise_for_status()
        return _parse_mcp_body(r), session_id


async def _sse_json_rpc(
    sse_endpoint: str, body: dict, auth_headers: dict, timeout: float = 6.0
) -> tuple[dict, str | None]:
    """
    Perform a JSON-RPC call over legacy SSE transport (GET /sse + POST /messages).

    The legacy MCP SSE protocol uses a split-channel design:
      • Server→Client: the GET /sse connection stays open and delivers responses
        as SSE events for the duration of the session.
      • Client→Server: POST /messages?session_id=xxx sends individual requests.

    The GET connection MUST remain open while the POST is sent, because the
    JSON-RPC response arrives through the SSE stream, not the POST response body.
    Closing the GET stream first (our earlier approach) invalidates the session,
    causing the POST to return 404.
    """
    import re
    from urllib.parse import urljoin, urlparse, urlunparse

    _hdrs_get = {"Accept": "text/event-stream", **auth_headers}
    _hdrs_post = {"Content-Type": "application/json", "Accept": "application/json", **auth_headers}

    async with httpx.AsyncClient(follow_redirects=True) as client:
        # Keep the SSE stream open for the entire operation
        async with client.stream("GET", sse_endpoint, headers=_hdrs_get, timeout=timeout) as resp:
            resp.raise_for_status()
            lines_iter = resp.aiter_lines()

            # Phase 1: read until we find the message endpoint URL
            msg_url: str | None = None
            session_id: str | None = None
            async for line in lines_iter:
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if "/messages" in data or "session_id" in data:
                        msg_url = urljoin(sse_endpoint, data)
                        m = re.search(r"session_id=([^&\s]+)", data)
                        if m:
                            session_id = m.group(1)
                        break

            if not msg_url:
                raise RuntimeError(
                    "SSE transport: could not obtain message endpoint from /sse stream. "
                    "Server may be offline or not yet started."
                )

            # Strip trailing slash from PATH only — the URL looks like
            # http://host/messages/?session_id=abc so rstrip("/") on the full
            # string never reaches the slash before the "?".
            _p = urlparse(msg_url)
            _clean_url = urlunparse(_p._replace(path=_p.path.rstrip("/")))

            # Phase 2: POST request while the GET stream is still alive
            r_post = await client.post(_clean_url, json=body, headers=_hdrs_post, timeout=timeout)
            # The POST typically returns 202 Accepted with no body; the actual
            # JSON-RPC response arrives through the still-open SSE stream below.
            if r_post.status_code not in (200, 202, 204):
                r_post.raise_for_status()

            # If the server sent the response directly in the POST body (non-standard
            # but some implementations do this), return it immediately.
            if r_post.status_code == 200 and r_post.content:
                try:
                    return r_post.json(), session_id
                except Exception:
                    pass

            # Phase 3: continue reading the SAME SSE iterator for the response event
            async for line in lines_iter:
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if data and data not in ("", "{}"):
                        try:
                            return json.loads(data), session_id
                        except Exception:
                            pass

    return {}, session_id


@app.get("/servers/{server_id}/tools", dependencies=[Depends(_require_admin)])
async def list_server_tools(server_id: str):
    """Probe an MCP server with tools/list (JSON-RPC 2.0) and return the tool list."""
    with get_engine().connect() as conn:
        try:
            row = conn.execute(
                text("SELECT endpoint, transport, api_key FROM mcp_servers WHERE id=:id"), {"id": server_id}
            ).fetchone()
        except Exception:
            row = conn.execute(
                text("SELECT endpoint, transport FROM mcp_servers WHERE id=:id"), {"id": server_id}
            ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")
    endpoint  = row.endpoint
    transport = (row._mapping.get("transport") or "streamable-http").lower()
    # Mint a short-lived RS256 JWT scoped to this server (MCP servers run FastMCP
    # JWTVerifier and reject the raw static api_key hex string — it is not a JWT).
    try:
        actual_key = generate_server_token(server_id, sub="hub-admin-probe", roles=["admin"], expires_hours=1)
        key_source = "hub-minted-jwt"
    except Exception:
        _raw = (row._mapping.get("api_key") or "") if row else ""
        actual_key = _raw or MCP_API_KEY
        key_source = "per-server-db" if _raw else ("env-MCP_API_KEY" if MCP_API_KEY else "none")
    _auth = {"Authorization": f"Bearer {actual_key}"} if actual_key else {}
    _body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    _req_headers = {"Content-Type": "application/json", **_auth}
    request_detail = _make_request_detail("POST", endpoint, _req_headers, _body)
    t0 = time.monotonic()
    _probe_transport = transport
    try:
        if transport == "sse":
            try:
                data, session_id = await _sse_json_rpc(endpoint, _body, _auth)
            except Exception as _sse_err:
                # DB transport may be stale ('sse') while server runs streamable-HTTP — try both
                try:
                    data, session_id = await _mcp_json_rpc(endpoint, _body, _req_headers)
                    _probe_transport = "streamable-http (auto-detected; DB has 'sse')"
                except Exception:
                    raise _sse_err
        else:
            data, session_id = await _mcp_json_rpc(endpoint, _body, _req_headers)
        tools = data.get("result", {}).get("tools", [])
        return {
            "ok": True,
            "tools": tools,
            "count": len(tools),
            "latency_ms": round((time.monotonic() - t0) * 1000),
            "auth_used": bool(actual_key),
            "key_source": key_source,
            "key_hint":   (actual_key[:16] + "…") if actual_key else "not configured",
            "transport":  _probe_transport,
            "session_id": session_id or None,
            "request": request_detail,
            "response_body": data,
        }
    except Exception as exc:
        resp_body = None
        try:
            resp_body = exc.response.json() if hasattr(exc, "response") else None  # type: ignore[attr-defined]
        except Exception:
            pass
        return {
            "ok": False,
            "error": str(exc),
            "latency_ms": round((time.monotonic() - t0) * 1000),
            "auth_used": bool(actual_key),
            "key_source": key_source,
            "transport":  _probe_transport,
            "request": request_detail,
            "response_body": resp_body,
        }


@app.post("/servers/{server_id}/test", dependencies=[Depends(_require_admin)])
async def test_server_connectivity(server_id: str):
    """Probe an MCP server with a JSON-RPC ping and return connectivity status + request details."""
    with get_engine().connect() as conn:
        try:
            row = conn.execute(
                text("SELECT endpoint, transport, api_key FROM mcp_servers WHERE id=:id"),
                {"id": server_id},
            ).fetchone()
        except Exception:
            row = conn.execute(
                text("SELECT endpoint, transport FROM mcp_servers WHERE id=:id"),
                {"id": server_id},
            ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")
    endpoint = row.endpoint
    transport = (row._mapping.get("transport") or "streamable-http").lower()
    try:
        actual_key = generate_server_token(server_id, sub="hub-admin-probe", roles=["admin"], expires_hours=1)
        key_source = "hub-minted-jwt"
    except Exception:
        _raw = (row._mapping.get("api_key") or "") if row else ""
        actual_key = _raw or MCP_API_KEY
        key_source = "per-server-db" if _raw else ("env-MCP_API_KEY" if MCP_API_KEY else "none")
    _auth = {"Authorization": f"Bearer {actual_key}"} if actual_key else {}
    _body = {"jsonrpc": "2.0", "id": "connectivity-test", "method": "ping", "params": {}}
    _req_headers = {"Content-Type": "application/json", **_auth}
    request_detail = _make_request_detail("POST", endpoint, _req_headers, _body)
    t0 = time.monotonic()
    _probe_transport = transport
    try:
        if transport == "sse":
            try:
                data, session_id = await _sse_json_rpc(endpoint, _body, _auth)
            except Exception as _sse_err:
                try:
                    data, session_id = await _mcp_json_rpc(endpoint, _body, _req_headers)
                    _probe_transport = "streamable-http (auto-detected; DB has 'sse')"
                except Exception:
                    raise _sse_err
        else:
            data, session_id = await _mcp_json_rpc(endpoint, _body, _req_headers)
        latency = round((time.monotonic() - t0) * 1000)
        return {
            "ok": True,
            "status_code": 200,
            "status_text": "HTTP 200",
            "latency_ms": latency,
            "endpoint": endpoint,
            "auth_used": bool(actual_key),
            "key_source": key_source,
            "key_hint":   (actual_key[:16] + "…") if actual_key else "not configured",
            "transport":  _probe_transport,
            "session_id": session_id or None,
            "request": request_detail,
            "response_body": data,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "latency_ms": round((time.monotonic() - t0) * 1000),
            "endpoint": endpoint,
            "auth_used": bool(actual_key),
            "key_source": key_source,
            "transport":  _probe_transport,
            "request": request_detail,
            "response_body": None,
        }


class ProbeBody(BaseModel):
    custom_body: dict | None = None
    custom_headers: dict | None = None  # additional/override headers (merged on top of defaults)


@app.post("/servers/{server_id}/probe", dependencies=[Depends(_require_admin)])
async def probe_server_custom(server_id: str, probe: ProbeBody | None = None):
    """Send a custom JSON-RPC body/headers to an MCP server. Used by the admin UI 'Edit & re-run'."""
    with get_engine().connect() as conn:
        try:
            row = conn.execute(
                text("SELECT endpoint, transport, api_key FROM mcp_servers WHERE id=:id"), {"id": server_id}
            ).fetchone()
        except Exception:
            row = conn.execute(
                text("SELECT endpoint, transport FROM mcp_servers WHERE id=:id"), {"id": server_id}
            ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")
    endpoint  = row.endpoint
    transport = (row._mapping.get("transport") or "streamable-http").lower()
    try:
        actual_key = generate_server_token(server_id, sub="hub-admin-probe", roles=["admin"], expires_hours=1)
        key_source = "hub-minted-jwt"
    except Exception:
        _raw = (row._mapping.get("api_key") or "") if row else ""
        actual_key = _raw or MCP_API_KEY
        key_source = "per-server-db" if _raw else ("env-MCP_API_KEY" if MCP_API_KEY else "none")
    _auth = {"Authorization": f"Bearer {actual_key}"} if actual_key else {}
    _body = (probe.custom_body if probe and probe.custom_body else
             {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    # Base headers; custom_headers from UI override/add on top
    _req_headers = {"Content-Type": "application/json", **_auth,
                    **(probe.custom_headers or {})}
    request_detail = _make_request_detail("POST", endpoint, _req_headers, _body)
    t0 = time.monotonic()
    _probe_transport = transport
    try:
        if transport == "sse":
            try:
                data, session_id = await _sse_json_rpc(endpoint, _body, _req_headers)
            except Exception as _sse_err:
                try:
                    data, session_id = await _mcp_json_rpc(endpoint, _body, _req_headers)
                    _probe_transport = "streamable-http (auto-detected)"
                except Exception:
                    raise _sse_err
        else:
            data, session_id = await _mcp_json_rpc(endpoint, _body, _req_headers)
        return {
            "ok": True,
            "latency_ms": round((time.monotonic() - t0) * 1000),
            "key_source": key_source,
            "transport":  _probe_transport,
            "request": request_detail,
            "response_body": data,
        }
    except Exception as exc:
        resp_body = None
        try:
            resp_body = exc.response.json() if hasattr(exc, "response") else None  # type: ignore[attr-defined]
        except Exception:
            pass
        return {
            "ok": False,
            "error": str(exc),
            "latency_ms": round((time.monotonic() - t0) * 1000),
            "key_source": key_source,
            "transport":  _probe_transport,
            "request": request_detail,
            "response_body": resp_body,
        }


@app.post("/api/hub/refresh", dependencies=[Depends(_require_admin)])
async def hub_cache_refresh():
    """Invalidate the in-process hub cache; next /discover re-reads from MySQL."""
    _invalidate_hub_cache()
    log_event("admin", action="cache_refresh")
    return {"ok": True}


@app.post("/api/hub/rotate-server-keys", dependencies=[Depends(_require_admin)])
async def rotate_server_keys():
    """Generate a fresh unique key for every MCP server in MySQL.

    After calling this, restart all MCP servers so they reload their key
    via _load_server_key() (the key is cached for process lifetime).
    """
    import secrets as _sec2
    updated = []
    try:
        with get_engine().begin() as conn:
            rows = conn.execute(text("SELECT id FROM mcp_servers")).fetchall()
            for row in rows:
                new_key = "srv-" + _sec2.token_hex(32)
                conn.execute(text(
                    "UPDATE mcp_servers SET api_key=:key WHERE id=:id"
                ), {"key": new_key, "id": row[0]})
                updated.append({"id": row[0], "key_hint": new_key[:10] + "…"})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Key rotation failed: {exc}")
    global _hub_cache, _hub_cache_at
    _hub_cache = None
    _hub_cache_at = 0.0
    log_event("admin", action="rotate_server_keys", count=len(updated))
    return {"rotated": len(updated), "servers": updated,
            "note": "Restart all MCP servers for new keys to take effect"}


# ---------------------------------------------------------------------------
# MCP Server Credential Management
# Per-server api_key stored in mcp_servers.api_key column.
# Agent uses per-server key over the shared MCP_API_KEY env var.
# ---------------------------------------------------------------------------

class McpCredentialRequest(BaseModel):
    api_key: str
    expires_hours: int | None = None


@app.get("/api/mcp-credentials", dependencies=[Depends(_require_admin)])
async def list_mcp_credentials():
    """List all MCP servers with credential status (keys redacted). Admin only."""
    with get_engine().connect() as conn:
        try:
            rows = conn.execute(
                text("SELECT id, name, endpoint, api_key, api_key_expires FROM mcp_servers ORDER BY id")
            ).fetchall()
            has_key_col = True
        except Exception:
            rows = conn.execute(
                text("SELECT id, name, endpoint FROM mcp_servers ORDER BY id")
            ).fetchall()
            has_key_col = False
    result = []
    for row in rows:
        key = (row._mapping.get("api_key") or "") if has_key_col else ""
        result.append({
            "id":          row.id,
            "name":        row.name,
            "endpoint":    row.endpoint,
            "api_key_set": bool(key),
            "api_key_hint": (key[:8] + "...") if len(key) > 8 else ("not set" if not key else key),
            "api_key_expires": str(row._mapping.get("api_key_expires")) if has_key_col and row._mapping.get("api_key_expires") else None,
        })
    return {"servers": result}


@app.put("/api/mcp-credentials/{server_id}", dependencies=[Depends(_require_admin)])
async def set_mcp_credential(server_id: str, req: McpCredentialRequest):
    """Set or rotate the api_key for an MCP server. Admin only.

    The new key is served to the agent on the next /discover call
    (cache is invalidated automatically).
    """
    expires_at = None
    if req.expires_hours:
        from datetime import datetime, timedelta
        expires_at = datetime.utcnow() + timedelta(hours=req.expires_hours)

    with get_engine().begin() as conn:
        result = conn.execute(
            text(
                "UPDATE mcp_servers SET api_key = :key, api_key_expires = :exp, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = :id"
            ),
            {"key": req.api_key, "exp": expires_at, "id": server_id},
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")

    _invalidate_hub_cache()
    log_event("admin", action="mcp_credential_rotated", server_id=server_id)
    return {
        "ok":        True,
        "server_id": server_id,
        "api_key_hint": req.api_key[:8] + "...",
        "expires_at":   str(expires_at) if expires_at else None,
        "note":         "Cache invalidated — next /discover returns updated credential",
    }


@app.delete("/api/mcp-credentials/{server_id}", dependencies=[Depends(_require_admin)])
async def clear_mcp_credential(server_id: str):
    """Remove the per-server api_key; agent falls back to MCP_API_KEY env var. Admin only."""
    with get_engine().begin() as conn:
        result = conn.execute(
            text(
                "UPDATE mcp_servers SET api_key = NULL, api_key_expires = NULL, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = :id"
            ),
            {"id": server_id},
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")

    _invalidate_hub_cache()
    log_event("admin", action="mcp_credential_cleared", server_id=server_id)
    return {"ok": True, "server_id": server_id,
            "note": "api_key cleared — agent will use MCP_API_KEY env var"}


@app.post("/api/auth/token", dependencies=[Depends(_require_admin)])
async def generate_jwt_token(req: TokenCreateRequest):
    """Mint a signed JWT for hub or MCP access."""
    try:
        from auth import generate_token as _gen
        token = _gen(
            sub=req.sub,
            roles=req.roles,
            audience=req.audience,
            server_id=req.server_id,
            expires_hours=req.hours,
        )
        exp = int(time.time()) + req.hours * 3600
        return {
            "token": token,
            "sub": req.sub,
            "roles": req.roles,
            "audience": req.audience,
            "server_id": req.server_id,
            "expires_at": exp,
            "hours": req.hours,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Token generation failed: {exc}")


@app.get("/api/auth/status", dependencies=[Depends(_require_admin)])
async def get_auth_status():
    """Return current auth configuration status for the Admin UI Auth Flow panel."""
    _jwt_secret    = os.getenv("JWT_SECRET", "")
    _hub_api_key   = os.getenv("HUB_API_KEY", "")
    _mcp_api_key   = os.getenv("MCP_API_KEY", "") or MCP_API_KEY
    _mcp_jwt_sec   = os.getenv("MCP_JWT_SECRET", "")
    _admin_user    = os.getenv("HUB_ADMIN_USERNAME", "admin")
    _admin_pass    = os.getenv("HUB_ADMIN_PASSWORD", "")
    _mcp_roles     = os.getenv("MCP_API_KEY_ROLES", "agent")
    server_count   = 0
    servers_with_key = 0
    server_hints: list[dict] = []
    try:
        with get_engine().connect() as conn:
            try:
                row = conn.execute(
                    text("SELECT COUNT(*) FROM mcp_servers WHERE is_active=1")
                ).fetchone()
                server_count = row[0] if row else 0
            except Exception:
                pass
            try:
                row2 = conn.execute(
                    text("SELECT COUNT(*) FROM mcp_servers "
                         "WHERE api_key IS NOT NULL AND api_key != '' AND is_active=1")
                ).fetchone()
                servers_with_key = row2[0] if row2 else 0
            except Exception:
                servers_with_key = 0
            try:
                srv_rows = conn.execute(text(
                    "SELECT id, name, api_key FROM mcp_servers "
                    "WHERE is_active=1 ORDER BY id"
                )).fetchall()
                for sr in srv_rows:
                    ak = sr[2] or ""
                    server_hints.append({
                        "id": sr[0],
                        "name": sr[1],
                        "key_set": bool(ak),
                        "key_hint": (ak[:10] + "…") if len(ak) > 10 else (ak or "not set"),
                        "key_unique": ak.startswith("srv-"),
                    })
            except Exception:
                pass
    except Exception:
        pass
    return {
        "auth_enabled":     AUTH_ENABLED,
        "auth_provider":    AUTH_PROVIDER,
        "jwt_secret":       bool(_jwt_secret),
        "jwt_secret_hint":  (_jwt_secret[:4] + "…") if _jwt_secret else None,
        "hub_api_key":      bool(_hub_api_key),
        "hub_api_key_hint": (_hub_api_key[:8] + "…") if _hub_api_key else None,
        "mcp_api_key":      bool(_mcp_api_key),
        "mcp_api_key_hint": (_mcp_api_key[:8] + "…") if _mcp_api_key else None,
        "mcp_jwt_secret":   bool(_mcp_jwt_sec),
        "mcp_api_key_roles": _mcp_roles,
        "chat_jwt_secret":  bool(_jwt_secret),
        "hub_admin_user":   bool(_admin_user),
        "hub_admin_pass":   bool(_admin_pass),
        "active_servers":   server_count,
        "servers_with_key": servers_with_key,
        "servers":          server_hints,
    }


@app.post("/api/admin/login")
async def hub_admin_login(req: HubLoginRequest):
    """Username/password login for Hub Admin Console — returns an 8-hour admin JWT."""
    if req.username != HUB_ADMIN_USERNAME or req.password != HUB_ADMIN_PASSWORD:
        log_event("auth", valid=False, sub=req.username, roles=[], token_type="password",
                  endpoint="/api/admin/login", method="POST", provider="local")
        raise HTTPException(status_code=401, detail="Invalid credentials")
    try:
        from auth import generate_token as _gen, _DEV_MODE_ACTIVE as _dev_mode
        if _dev_mode:
            # Dev mode: JWT_SECRET not configured — hub accepts any token.
            # Return a simple opaque token; verify_token will pass it in dev mode.
            import secrets as _sec
            token = "dev-admin-" + _sec.token_hex(20)
        else:
            token = _gen(sub=req.username, roles=["admin"], expires_hours=8)
        log_event("auth", valid=True, sub=req.username, roles=["admin"], token_type="password",
                  endpoint="/api/admin/login", method="POST", provider="local")
        return {"token": token, "sub": req.username, "roles": ["admin"], "dev_mode": bool(_dev_mode)}
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Admin console HTML
# ---------------------------------------------------------------------------

_ADMIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FAB MCP Hub — Admin Console</title>
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#1c2130;--bg4:#21262d;--bd:#30363d;
  --tx:#e6edf3;--mu:#8b949e;--gr:#2ea043;--grh:#3fb950;--rd:#da3633;--rdh:#f85149;
  --yw:#d29922;--bl:#388bfd;--blh:#58a6ff;--pu:#8b5cf6;}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
  background:var(--bg);color:var(--tx);min-height:100vh;font-size:14px}
code{font-family:monospace;font-size:12px;background:var(--bg4);padding:1px 5px;border-radius:3px;color:var(--blh)}
/* Login */
#loginWrap{display:flex;align-items:center;justify-content:center;min-height:100vh}
.lc{background:var(--bg2);border:1px solid var(--bd);border-radius:12px;padding:40px;width:420px;max-width:95vw;text-align:center}
.lc-logo{font-size:24px;font-weight:700;margin-bottom:6px}
.lc-sub{color:var(--mu);margin-bottom:20px;font-size:13px}
.hint{background:var(--bg3);border:1px solid var(--bd);border-radius:8px;padding:12px;
  text-align:left;margin-bottom:16px;font-size:12px;color:var(--mu);line-height:1.8}
.lc input{width:100%;padding:10px 12px;background:var(--bg3);border:1px solid var(--bd);
  border-radius:8px;color:var(--tx);font-size:13px;margin-bottom:10px;outline:none}
.lc input:focus{border-color:var(--bl)}
.lc .sbtn{width:100%;padding:10px;background:var(--gr);border:none;border-radius:8px;
  color:#fff;font-weight:600;font-size:14px;cursor:pointer}
.lc .sbtn:hover{background:var(--grh)}
.err-msg{color:var(--rd);font-size:13px;font-weight:600;margin-top:10px;min-height:20px;padding:6px 0}
/* Shell */
#adminWrap{display:none;min-height:100vh;flex-direction:column}
header{background:var(--bg2);border-bottom:1px solid var(--bd);height:52px;
  display:flex;align-items:center;padding:0 20px;gap:14px;flex-shrink:0}
.hl{font-weight:700;font-size:15px;white-space:nowrap}.hl span{color:var(--blh)}
nav{display:flex;gap:2px;flex:1}
.tb{padding:6px 13px;background:none;border:none;border-radius:6px;color:var(--mu);
  cursor:pointer;font-size:13px;font-weight:500}
.tb:hover{background:var(--bg3);color:var(--tx)}.tb.act{background:var(--bg3);color:var(--tx)}
.hu{font-size:12px;color:var(--mu)}
.lbtn{padding:5px 12px;background:var(--bg3);border:1px solid var(--bd);border-radius:6px;
  color:var(--mu);cursor:pointer;font-size:12px}
.lbtn:hover{color:var(--rd);border-color:var(--rd)}
main{flex:1;padding:20px;overflow:auto;max-width:1400px;margin:0 auto;width:100%}
.tp{display:none}.tp.act{display:block}
/* Cards */
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin-bottom:20px}
.card{background:var(--bg2);border:1px solid var(--bd);border-radius:10px;padding:18px}
.cv{font-size:26px;font-weight:700;margin:6px 0}.cl{font-size:11px;color:var(--mu);text-transform:uppercase;letter-spacing:.5px}
.cs{font-size:12px;color:var(--mu)}.card.ok .cv{color:var(--gr)}.card.info .cv{color:var(--bl)}
.card.warn .cv{color:var(--yw)}
/* Section */
.sec{background:var(--bg2);border:1px solid var(--bd);border-radius:10px;margin-bottom:16px;overflow:hidden}
.sec-h{padding:14px 18px;border-bottom:1px solid var(--bd);display:flex;align-items:center;justify-content:space-between}
.sec-h h3{font-size:14px;font-weight:600}
.sec-b{padding:18px}
.tbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
/* Buttons */
.btn{padding:7px 14px;border-radius:7px;border:1px solid var(--bd);background:var(--bg3);
  color:var(--tx);cursor:pointer;font-size:13px;font-weight:500}
.btn:hover{border-color:var(--mu)}
.btn-p{background:var(--gr);border-color:var(--gr);color:#fff}.btn-p:hover{background:var(--grh)}
.btn-d{background:transparent;border-color:var(--rd);color:var(--rd)}.btn-d:hover{background:var(--rd);color:#fff}
.btn-s{padding:4px 10px;font-size:12px;border-radius:5px}
/* Table */
.tbl{width:100%;border-collapse:collapse;font-size:13px}
.tbl th{text-align:left;padding:9px 14px;color:var(--mu);font-weight:500;border-bottom:1px solid var(--bd);
  font-size:11px;text-transform:uppercase;letter-spacing:.5px}
.tbl td{padding:9px 14px;border-bottom:1px solid var(--bg3);vertical-align:middle}
.tbl tr:last-child td{border-bottom:none}.tbl tr:hover td{background:var(--bg3)}
/* Badges */
.bx{display:inline-flex;align-items:center;padding:2px 8px;border-radius:20px;font-size:11px;font-weight:600}
.ok{background:rgba(46,160,67,.18);color:var(--gr)}.off{background:rgba(218,54,51,.12);color:var(--rd)}
.bsse{background:rgba(56,139,253,.15);color:var(--bl)}.bhttp{background:rgba(139,92,246,.15);color:var(--pu)}
.bdev{background:rgba(240,198,69,.12);color:var(--yw)}.badm{background:rgba(139,92,246,.18);color:var(--pu)}
.bagt{background:rgba(56,139,253,.15);color:var(--bl)}
/* Form */
.fr{margin-bottom:12px}.fr label{display:block;font-size:12px;color:var(--mu);margin-bottom:4px;font-weight:500}
.fr input,.fr select,.fr textarea{width:100%;padding:8px 11px;background:var(--bg3);border:1px solid var(--bd);
  border-radius:7px;color:var(--tx);font-size:13px;outline:none;resize:vertical}
.fr input:focus,.fr select:focus,.fr textarea:focus{border-color:var(--bl)}
.fr textarea{min-height:70px}
.frow{display:flex;gap:12px}.frow .fr{flex:1}
/* Modal */
#mo,#co,#chmo,#toolsmo{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:100;
  align-items:center;justify-content:center}
.mdl{background:var(--bg2);border:1px solid var(--bd);border-radius:12px;width:600px;
  max-width:95vw;max-height:92vh;overflow-y:auto}
.mh{padding:18px 22px;border-bottom:1px solid var(--bd);display:flex;justify-content:space-between;align-items:center}
.mh h3{font-size:15px;font-weight:600}
.mc{padding:22px}.mf{padding:14px 22px;border-bottom:0;border-top:1px solid var(--bd);
  display:flex;justify-content:flex-end;gap:10px}
.xbtn{background:none;border:none;color:var(--mu);cursor:pointer;font-size:20px;line-height:1}
.xbtn:hover{color:var(--tx)}
/* Logs */
.le{font-size:12px;font-family:monospace;padding:5px 14px;border-bottom:1px solid var(--bg3);
  display:flex;gap:10px;align-items:flex-start}
.le:last-child{border-bottom:none}
.lts{color:var(--mu);white-space:nowrap;width:85px;flex-shrink:0}
.lty{width:62px;flex-shrink:0}
.lty.auth{color:var(--pu)}.lty.routing{color:var(--bl)}.lty.request{color:var(--mu)}
.lty.error{color:var(--rd)}.lty.admin{color:var(--gr)}
.lb{flex:1;color:var(--mu);word-break:break-all}.lb b{color:var(--tx)}
/* Token output */
.tout{background:var(--bg3);border:1px solid var(--bd);border-radius:8px;padding:12px;
  font-size:11px;font-family:monospace;word-break:break-all;color:var(--gr);
  margin-top:10px;max-height:100px;overflow-y:auto;display:none}
.kv{width:100%;border-collapse:collapse}
.kv td{padding:7px 0;border-bottom:1px solid var(--bg3);font-size:13px;vertical-align:top}
.kv td:first-child{color:var(--mu);width:160px;font-size:12px}.kv tr:last-child td{border-bottom:none}
</style>
</head>
<body>
<!-- LOGIN -->
<div id="loginWrap">
  <div class="lc">
    <div class="lc-logo">&#x2B21; FAB MCP Hub</div>
    <div class="lc-sub">Admin Console</div>
    <div class="hint" style="text-align:left">
      Default credentials: <b>admin / admin</b><br>
      Override via <code>HUB_ADMIN_USERNAME</code> / <code>HUB_ADMIN_PASSWORD</code> in <code>.env</code>
    </div>
    <form id="loginForm" action="/admin/login" method="POST"
          onsubmit="event.preventDefault();_submitLogin();">
      <input name="username" id="un" type="text" placeholder="Username" value="admin" autocomplete="username">
      <input name="password" id="pw" type="password" placeholder="Password" value="admin" autocomplete="current-password">
      <button type="submit" class="sbtn">Sign in</button>
    </form>
    <div id="tokenForm" style="display:none">
      <div class="hint">Mint: <code>python hub_service/auth.py --sub admin --roles admin --hours 24</code></div>
      <input id="ti" type="password" placeholder="Paste admin JWT or API key&hellip;" autocomplete="off">
      <button class="sbtn" onclick="doLoginToken()">Sign in with token</button>
    </div>
    <div id="le2" class="err-msg"></div>
    <div style="margin-top:12px;font-size:12px;color:var(--mu)">
      <a id="togLink" href="#" onclick="toggleLoginMode(event)" style="color:var(--bl)">Use token instead &rarr;</a>
    </div>
  </div>
</div>

<!-- SHELL -->
<div id="adminWrap" style="flex-direction:column">
  <header>
    <div class="hl">&#x2B21; <span>FAB MCP Hub</span> Admin</div>
    <nav>
      <button class="tb act" data-t="dash" onclick="st('dash')">Dashboard</button>
      <button class="tb" data-t="srv" onclick="st('srv')">MCP Servers</button>
      <button class="tb" data-t="logs" onclick="st('logs')">Observability</button>
      <button class="tb" data-t="auth" onclick="st('auth')">Auth &amp; Tokens</button>
    </nav>
    <span class="hu" id="hu"></span>
    <button class="lbtn" onclick="doLogout()">Logout</button>
  </header>
  <main>
    <!-- DASHBOARD -->
    <div id="tp-dash" class="tp act">
      <div class="cards" id="dcards"></div>
      <div class="sec"><div class="sec-h"><h3>Registered Servers</h3></div>
        <table class="tbl"><thead><tr><th>ID</th><th>Name</th><th>Transport</th><th>Status</th><th>Endpoint</th></tr></thead>
        <tbody id="dstbl"></tbody></table></div>
    </div>
    <!-- SERVERS -->
    <div id="tp-srv" class="tp">
      <div class="tbar">
        <button class="btn btn-p" onclick="openM(null)">+ Add MCP Server</button>
        <button class="btn" onclick="rfCache()">&#8635; Refresh Cache</button>
          <button class="btn btn-s" onclick="rotateKeys()" style="background:var(--yw);color:#000" title="Generate unique per-server API keys for ALL servers. Requires MCP server restart.">&#128273; Rotate All Keys</button>
        <span id="sci" style="font-size:12px;color:var(--mu)"></span>
      </div>
      <div class="sec" style="padding:0">
        <table class="tbl"><thead><tr><th>ID</th><th>Name</th><th>Transport</th><th>Endpoint</th>
          <th>Status</th><th>Key</th><th>Capability</th><th style="text-align:right">Actions</th></tr></thead>
        <tbody id="stbl"></tbody></table>
      </div>
    </div>
    <!-- LOGS -->
    <div id="tp-logs" class="tp">
      <div class="tbar">
        <select id="ltf" class="btn" onchange="loadLogs()" style="cursor:pointer">
          <option value="">All types</option><option value="auth">auth</option>
          <option value="request">request</option><option value="request_detail">request_detail</option>
          <option value="routing">routing</option>
          <option value="admin">admin</option><option value="error">error</option>
        </select>
        <select id="lcf" class="btn" onchange="loadLogs()" style="cursor:pointer">
          <option value="50">50 events</option><option value="100" selected>100</option>
          <option value="250">250</option><option value="500">500</option>
        </select>
        <button class="btn" onclick="loadLogs()">&#8635; Refresh</button>
        <label style="font-size:13px;cursor:pointer;display:flex;align-items:center;gap:6px">
          <input type="checkbox" id="ar" onchange="togAR()"> Auto 5s
        </label>
        <span id="lm" style="font-size:12px;color:var(--mu);margin-left:auto"></span>
      </div>
      <div class="sec" style="padding:0"><div id="lc2" style="max-height:580px;overflow-y:auto"></div></div>
    </div>
    <!-- AUTH & TOKENS -->
    <div id="tp-auth" class="tp">
      <div class="sec" style="margin-bottom:16px">
        <div class="sec-h"><h3>Current Configuration</h3></div>
        <div class="sec-b"><div id="acv"></div></div>
      </div>
      <div class="sec" style="margin-bottom:16px">
        <div class="sec-h"><h3>&#128273; Generate JWT Token</h3></div>
        <div class="sec-b">
          <p style="color:var(--mu);font-size:13px;margin-bottom:14px">
            Mint a short-lived HS256 JWT. Set <code>HUB_API_KEY</code> to an
            <em>agent</em>-role token for service auth; use <em>admin</em>-role
            token for this console.
          </p>
          <div class="frow">
            <div class="fr"><label>Subject (sub)</label>
              <input id="ts" value="fab-agent" placeholder="e.g. fab-agent"></div>
            <div class="fr"><label>Roles (comma-sep)</label>
              <input id="tr2" value="agent" placeholder="agent or admin,agent"></div>
            <div class="fr" style="max-width:130px"><label>Expires (hours)</label>
              <input id="th" type="number" value="24" min="1" max="8760"></div>
          </div>
          <button class="btn btn-p" onclick="genTok()">&#128273; Generate</button>
          <div id="tout" class="tout"></div>
          <div id="tcopy" style="display:none;margin-top:8px;display:none;gap:8px;align-items:center">
            <button class="btn btn-s" onclick="cpTok()">&#128203; Copy</button>
            <span id="ti2" style="font-size:12px;color:var(--mu)"></span>
          </div>
        </div>
      </div>
      <div class="sec">
        <div class="sec-h"><h3>&#128271; Why does every token start with <code>eyJhbGciOi</code>?</h3></div>
        <div class="sec-b" style="font-size:13px;color:var(--mu);line-height:1.8">
          <p>Every JWT has three base64url segments separated by dots: <b>header · payload · signature</b>.</p>
          <p style="margin:10px 0">The header for HS256 is always <code>{"alg":"HS256","typ":"JWT"}</code>, which
          base64url-encodes to the constant prefix <code style="color:var(--yw)">eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9</code>.
          The trace hint shows the first 10 characters of the token — which is always this header.
          <b>Tokens are NOT being reused</b>: the payload (sub, roles, exp) and HMAC signature are unique per token.</p>
          <table class="kv" style="margin-top:12px">
            <tr><td>Standard approach</td><td>Set <code>HUB_API_KEY</code> and <code>MCP_API_KEY</code> to a
              <em>JWT</em> generated here (agent role) so that sub and roles appear in every auth log entry.</td></tr>
            <tr><td>Per-user vs service</td><td>Browser&rarr;Chat: user JWT (sub=username).<br>
              Agent&rarr;Hub: user JWT forwarded (hub sees real user).<br>
              Agent&rarr;MCP: service JWT (sub=fab-agent) — MCP servers verify service identity, not user identity.</td></tr>
            <tr><td>Token lifespan</td><td>Chat sessions: 8 h (hardcoded).<br>
              Service tokens (HUB_API_KEY / MCP_API_KEY): 24 h or longer, rotated periodically.</td></tr>
            <tr><td>RBAC enforcement</td><td>admin &gt; agent &gt; readonly. Use <code>require_role()</code> inside
              MCP tool functions to enforce per-tool RBAC.</td></tr>
          </table>
        </div>
      </div>
      <div class="sec" style="margin-top:16px">
        <div class="sec-h" style="display:flex;align-items:center;justify-content:space-between">
          <h3>&#128268; Live Auth Configuration</h3>
          <button class="btn btn-s" onclick="loadAuthStatus()">&#8635; Refresh</button>
        </div>
        <div id="auth-status-panel" style="padding:14px 18px">
          <div style="color:var(--mu);font-size:13px">Loading…</div>
        </div>
      </div>
      <div class="sec" style="margin-top:16px">
        <div class="sec-h"><h3>&#9654; End-to-End Auth Flow — Step by Step</h3></div>
        <div class="sec-b" style="font-size:12px;line-height:1.9">

          <!-- LAYER 1 -->
          <div style="border:1px solid var(--bd);border-radius:8px;margin-bottom:12px;overflow:hidden">
            <div style="background:var(--bg3);padding:8px 14px;font-size:11px;font-weight:600;letter-spacing:.5px;color:var(--yw);text-transform:uppercase">Layer 1 — Browser → Chat Server (User JWT)</div>
            <div style="padding:10px 14px">
              <table class="kv" style="margin:0;font-size:12px">
                <tr><td style="white-space:nowrap">Step 1</td><td>Browser POSTs <code>POST /api/admin/login</code> with username + password</td></tr>
                <tr><td>Step 2</td><td>Chat server verifies credentials → generates JWT signed with <code>JWT_SECRET</code>:
                  <code style="display:block;margin-top:4px;font-size:11px">{ "sub": "admin", "roles": ["admin"], "exp": now+8h, "iss": "fab-chat" }</code></td></tr>
                <tr><td>Step 3</td><td>Browser stores JWT in <code>sessionStorage</code></td></tr>
                <tr><td>Step 4</td><td>Every chat request: <code>Authorization: Bearer &lt;JWT&gt;</code> in header</td></tr>
                <tr><td>Validated by</td><td>Chat server: <code>jwt.decode(token, JWT_SECRET, algorithms=["HS256"])</code></td></tr>
                <tr><td>Timeline</td><td>auth_hop [browser → chat_server] — click ▶ to see full token + JWT anatomy (3-part decode)</td></tr>
              </table>
            </div>
          </div>

          <!-- LAYER 2 -->
          <div style="border:1px solid var(--bd);border-radius:8px;margin-bottom:12px;overflow:hidden">
            <div style="background:var(--bg3);padding:8px 14px;font-size:11px;font-weight:600;letter-spacing:.5px;color:var(--bl);text-transform:uppercase">Layer 2 — Agent → Hub (Same User JWT forwarded)</div>
            <div style="padding:10px 14px">
              <table class="kv" style="margin:0;font-size:12px">
                <tr><td style="white-space:nowrap">Step 1</td><td>Chat server extracts <code>hub_token</code> from the incoming request's Authorization header (same JWT the browser sent)</td></tr>
                <tr><td>Step 2</td><td>Agent POSTs <code>POST /discover</code> with that JWT:
                  <code style="display:block;margin-top:4px;font-size:11px">Authorization: Bearer &lt;user-JWT&gt;<br>Body: { "intent": "query text" }</code></td></tr>
                <tr><td>Step 3</td><td>Hub validates the JWT with its own copy of <code>JWT_SECRET</code> → extracts sub, roles</td></tr>
                <tr><td>Step 4</td><td>Hub runs LLM routing → returns list of matching MCP server IDs + their endpoints + per-server api_keys</td></tr>
                <tr><td>Key source</td><td><code>HUB_API_KEY</code> env var (agent → hub static key) or forwarded user JWT</td></tr>
                <tr><td>Timeline</td><td>auth_hop [agent → hub] · routing · hub_loaded</td></tr>
              </table>
            </div>
          </div>

          <!-- LAYER 3 — MOST IMPORTANT -->
          <div style="border:1px solid var(--yw);border-radius:8px;margin-bottom:12px;overflow:hidden">
            <div style="background:rgba(210,153,34,.12);padding:8px 14px;font-size:11px;font-weight:600;letter-spacing:.5px;color:var(--yw);text-transform:uppercase">Layer 3 — Agent → MCP Server (Service JWT — how it is obtained)</div>
            <div style="padding:10px 14px">
              <table class="kv" style="margin:0;font-size:12px">
                <tr><td style="white-space:nowrap;color:var(--yw)">Key origin</td><td><b>This is NOT the user JWT.</b> It is a <i>service JWT</i> (sub=fab-agent) signed with a <b>separate secret</b> (<code>MCP_JWT_SECRET</code>).</td></tr>
                <tr><td>How to create</td><td>Use the <b>Generate JWT</b> panel on this page:
                  <br>Subject = <code>fab-agent</code>, Roles = <code>agent</code>, Hours = <code>24</code>
                  <br>OR run: <code>python hub_service/auth.py --sub fab-agent --roles agent --hours 24</code>
                  <br>→ copy the token to <code>MCP_API_KEY</code> in root <code>.env</code></td></tr>
                <tr><td>Per-server key</td><td><b>Each MCP server now gets its own unique key</b> stored in MySQL <code>mcp_servers.api_key</code>.
                  <br>At startup, the hub auto-generates a <code>srv-&lt;hex64&gt;</code> key for any server with no key.
                  <br>Use <b>&#128273; Rotate All Keys</b> button on Servers tab to regenerate all keys at once,
                  or <b>&#128273; Key button</b> per server → "Generate &amp; Fill" → Save Key for individual rotation.
                  <br>Agent receives the per-server key from <code>/discover</code> response and prefers it over <code>MCP_API_KEY</code> env var.</td></tr>
                <tr><td>Step 1 (connect)</td><td>Agent POSTs <code>initialize</code> to <code>&lt;endpoint&gt;/mcp/</code>:
                  <code style="display:block;margin-top:4px;font-size:11px">Authorization: Bearer &lt;service-JWT&gt;<br>Body: { "jsonrpc":"2.0", "method":"initialize", "params":{...} }</code>
                  Server responds with <code>mcp-session-id</code> header + server info</td></tr>
                <tr><td>Step 2 (list tools)</td><td><code>POST /mcp/</code> with <code>mcp-session-id</code> header + <code>tools/list</code> body</td></tr>
                <tr><td>Step 3 (call tool)</td><td><code>POST /mcp/</code> with <code>mcp-session-id</code> + <code>tools/call { name, arguments }</code></td></tr>
                <tr><td>Validated by</td><td>MCP server <code>BearerAuthMiddleware</code>: <code>jwt.decode(token, MCP_JWT_SECRET)</code> → stores claims in <code>_request_claims</code> ContextVar (asyncio-task-local)</td></tr>
                <tr><td>Timeline</td><td>auth_hop [agent → mcp] · mcp_connected (lists tools) · tool_call</td></tr>
              </table>
            </div>
          </div>

          <!-- LAYER 4 -->
          <div style="border:1px solid var(--bd);border-radius:8px;margin-bottom:12px;overflow:hidden">
            <div style="background:var(--bg3);padding:8px 14px;font-size:11px;font-weight:600;letter-spacing:.5px;color:var(--gr);text-transform:uppercase">Layer 4 — MCP Server per-tool RBAC (Same JWT re-used)</div>
            <div style="padding:10px 14px">
              <table class="kv" style="margin:0;font-size:12px">
                <tr><td style="white-space:nowrap">How it works</td><td>Each <code>tools/call</code> = a new HTTP POST → <code>BearerAuthMiddleware.dispatch()</code> runs again on every request</td></tr>
                <tr><td>Same JWT?</td><td><b>YES</b> — the same service JWT from Layer 3 is sent in every tool call Authorization header</td></tr>
                <tr><td>RBAC check</td><td><code>require_role("admin","agent")</code> reads <code>_request_claims.get()</code> (ContextVar already set by middleware) — no second JWT decode</td></tr>
                <tr><td>Audit</td><td><code>audit_log(tool_name, agent_sub, agent_roles)</code> records who called what</td></tr>
                <tr><td>Timeline</td><td>tool_rbac [🛡️] — shows RBAC result, same-token confirmation, DB storage location</td></tr>
              </table>
            </div>
          </div>

          <div style="padding:10px 14px;background:var(--bg3);border-radius:6px;border-left:3px solid var(--yw);font-size:12px;color:var(--mu);line-height:1.7">
            <b style="color:var(--tx)">Quick Setup Guide:</b><br>
            1. Generate a JWT here (sub=fab-agent, roles=agent) → paste into root <code>.env</code> as <code>MCP_API_KEY=&lt;token&gt;</code><br>
            2. Copy the same secret used to sign it into every MCP server's <code>.env</code> as <code>MCP_JWT_SECRET=&lt;same-secret&gt;</code><br>
            3. Optionally: set per-server keys via &#128273; Key button (generates &amp; saves a per-server JWT into MySQL)<br>
            4. The Chat UI timeline ▶ expand shows the exact JWT sent at each hop — use it to verify the chain
          </div>
        </div>
      </div>
      <div class="sec" style="margin-top:16px">
        <div class="sec-h"><h3>&#128204; Credential Registry — Where every key lives</h3></div>
        <div class="sec-b" style="font-size:13px">
          <p style="color:var(--mu);margin-bottom:14px">
            The system uses five credential layers. This table shows exactly where each key is stored,
            which file/column holds it, and what it authorises.
          </p>
          <table class="tbl" style="font-size:12px">
            <thead><tr>
              <th>Layer</th><th>Flow</th><th>Storage location</th><th>Key / Field</th><th>Purpose</th>
            </tr></thead>
            <tbody>
              <tr>
                <td><b>1 — Chat session</b></td>
                <td>Browser &#8594; Chat</td>
                <td><code>chat_service/.env</code></td>
                <td><code>JWT_SECRET</code></td>
                <td>Signs user session JWTs (8 h). Verified by chat_server.py on every request.</td>
              </tr>
              <tr>
                <td><b>2 — Agent &#8594; Hub</b></td>
                <td>Agent &#8594; Hub /discover</td>
                <td><code>.env</code> (root)</td>
                <td><code>HUB_API_KEY</code></td>
                <td>Bearer token agent sends with every /discover call. Hub validates against JWT_SECRET.</td>
              </tr>
              <tr>
                <td><b>3a — Per-server MCP</b></td>
                <td>Agent &#8594; MCP server</td>
                <td>MySQL <code>fab_semantic.mcp_servers.api_key</code></td>
                <td>&#128273; Key column (per row)</td>
                <td>Per-server auth key. Set via Admin UI &#8594; Servers &#8594; &#128273; Key button. Takes priority over env fallback.</td>
              </tr>
              <tr>
                <td><b>3b — Shared MCP fallback</b></td>
                <td>Agent &#8594; MCP server</td>
                <td><code>.env</code> (root) + each MCP server's <code>.env</code></td>
                <td><code>MCP_API_KEY</code></td>
                <td>Fallback when no per-server key. All MCP servers validate it against their <code>MCP_JWT_SECRET</code>.</td>
              </tr>
              <tr>
                <td><b>4 — External services</b></td>
                <td>MCP server &#8594; External API</td>
                <td>SQLite <code>tool_credentials.db</code> (per MCP server dir)</td>
                <td><code>tool_credentials</code> table</td>
                <td>Per-tool external API keys (e.g. weather, maps). Managed by external_service.py. Never exposed in JWT.</td>
              </tr>
            </tbody>
          </table>
          <div style="margin-top:14px;padding:10px 14px;background:var(--bg2);border-radius:6px;border-left:3px solid var(--yw);font-size:12px;color:var(--mu);line-height:1.7">
            <b style="color:var(--tx)">Key resolution order (agent.py):</b><br>
            1. MySQL <code>mcp_servers.api_key</code> for the specific server (set via &#128273; Key button above)<br>
            2. <code>MCP_API_KEY</code> env var (shared fallback for all servers)<br>
            3. No auth header sent (MCP_AUTH_ENABLED=false dev mode only)<br>
            <br>
            <b style="color:var(--tx)">Rotation:</b> Generate a new JWT in the panel above, then update HUB_API_KEY / MCP_API_KEY in <code>.env</code>
            and rotate per-server keys via the &#128273; Key button. No restart required for per-server keys (cache is invalidated automatically).
          </div>
        </div>
      </div>
    </div>
  </main>
</div>

<!-- SERVER MODAL -->
<div id="mo" onclick="closeM(event)">
  <div class="mdl" onclick="event.stopPropagation()">
    <div class="mh"><h3 id="mt">Add MCP Server</h3><button class="xbtn" onclick="closeM()">&#x2715;</button></div>
    <div class="mc">
      <div class="frow"><div class="fr"><label>Server ID *</label>
          <input id="fid" placeholder="e.g. my-server"></div>
        <div class="fr"><label>Display Name *</label>
          <input id="fnm" placeholder="e.g. My Server"></div></div>
      <div class="fr"><label>Endpoint URL *</label>
        <input id="fep" placeholder="http://localhost:8001/sse or http://host:9100/mcp/"></div>
      <div class="frow">
        <div class="fr"><label>Transport</label>
          <select id="ftr"><option value="sse">SSE</option><option value="streamable-http">Streamable HTTP</option></select></div>
        <div class="fr" style="display:flex;align-items:flex-end;padding-bottom:2px">
          <label style="display:flex;align-items:center;gap:8px;font-size:13px;cursor:pointer;color:var(--tx)">
            <input type="checkbox" id="fac" checked> Active</label></div></div>
      <div class="fr"><label>Capability</label><input id="fca" placeholder="One-line domain description"></div>
      <div class="fr"><label>Full Description</label>
        <textarea id="fds" placeholder="Detailed description of what this server provides"></textarea></div>
      <div class="fr"><label>Skills (comma-separated)</label>
        <input id="fsk" placeholder="e.g. weather, forecast, temperature"></div>
      <div class="fr"><label>Example Queries (comma-separated)</label>
        <input id="fex" placeholder="What is the weather in Tokyo?, Show forecast for London"></div>
      <div class="fr"><label>Start Command (optional)</label>
        <input id="fsc" placeholder="python mcp_server/weather_server.py 8001"></div>
    </div>
    <div class="mf"><button class="btn" onclick="closeM()">Cancel</button>
      <button class="btn btn-p" onclick="saveS()">Save Server</button></div>
  </div>
</div>

<!-- CONFIRM MODAL -->
<div id="co" onclick="closeC()">
  <div class="mdl" style="width:360px" onclick="event.stopPropagation()">
    <div class="mh"><h3>Confirm Delete</h3><button class="xbtn" onclick="closeC()">&#x2715;</button></div>
    <div class="mc"><p id="cm" style="font-size:14px;line-height:1.6"></p></div>
    <div class="mf"><button class="btn" onclick="closeC()">Cancel</button>
      <button class="btn btn-d" id="cok">Delete</button></div>
  </div>
</div>

<!-- CHANGELOG MODAL -->
<div id="chmo" onclick="closeCh(event)">
  <div class="mdl" style="width:740px" onclick="event.stopPropagation()">
    <div class="mh"><h3 id="chmt">Server Change History</h3><button class="xbtn" onclick="closeCh()">&#x2715;</button></div>
    <div id="chmc" style="max-height:70vh;overflow-y:auto"></div>
    <div class="mf"><button class="btn" onclick="closeCh()">Close</button></div>
  </div>
</div>

<!-- TOOLS MODAL -->
<div id="toolsmo" onclick="closeTls(event)" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:150;align-items:center;justify-content:center">
  <div style="background:var(--bg2);border:1px solid var(--bd);border-radius:10px;width:760px;max-width:96vw;max-height:88vh;overflow:hidden;display:flex;flex-direction:column" onclick="event.stopPropagation()">
    <div class="mh"><h3 id="tlsmt">Server Tools</h3><button class="xbtn" onclick="closeTls()">&#x2715;</button></div>
    <div id="tlsmc" style="overflow-y:auto;flex:1;min-height:0"></div>
    <div class="mf" style="flex-wrap:wrap;gap:6px">
      <button class="btn btn-s" onclick="showTools(_curToolsId)" style="font-size:12px">&#8635; Refresh</button>
      <button class="btn btn-s" id="tls-copy-curl" onclick="_tls_copyCurl(this)" style="font-size:12px">&#128203; Copy curl</button>
      <span style="flex:1"></span>
      <button class="btn" onclick="closeTls()">Close</button>
    </div>
  </div>
</div>

<div id="testmo" onclick="closeTmo(event)" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:150;align-items:center;justify-content:center">
  <div style="background:var(--bg2);border:1px solid var(--bd);border-radius:10px;width:720px;max-width:96vw;max-height:88vh;overflow:hidden;display:flex;flex-direction:column" onclick="event.stopPropagation()">
    <div class="mh"><h3 id="tmo-title">Test</h3><button class="xbtn" onclick="closeTmoStop()">&#x2715;</button></div>
    <div id="tmo-body" style="overflow-y:auto;flex:1;min-height:0"></div>
    <div class="mf" style="flex-wrap:wrap;gap:6px">
      <button class="btn btn-s" id="tmo-copy-curl" onclick="_tmo_copyCurl(this)" style="font-size:12px">&#128203; Copy curl</button>
      <button class="btn btn-s" id="tmo-auto-btn" onclick="_tmo_toggleAuto()" style="font-size:12px">&#9654; Auto-refresh off</button>
      <span style="flex:1"></span>
      <button class="btn" onclick="closeTmoStop()">Close</button>
    </div>
  </div>
</div>

<div id="detmo" onclick="closeDet(event)" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:150;align-items:center;justify-content:center">
  <div style="background:var(--bg2);border:1px solid var(--bd);border-radius:10px;width:660px;max-width:96vw;max-height:85vh;overflow:hidden;display:flex;flex-direction:column" onclick="event.stopPropagation()">
    <div class="mh"><h3 id="detmo-title">Server Details</h3><button class="xbtn" onclick="document.getElementById('detmo').style.display='none'">&#x2715;</button></div>
    <div id="detmo-body" style="overflow-y:auto;flex:1"></div>
    <div class="mf"><button class="btn" onclick="document.getElementById('detmo').style.display='none'">Close</button></div>
  </div>
</div>

<!-- CREDENTIALS MODAL -->
<div id="credmo" onclick="closeCreds(event)" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:160;align-items:center;justify-content:center">
  <div style="background:var(--bg2);border:1px solid var(--bd);border-radius:10px;width:520px;max-width:96vw;max-height:85vh;overflow:hidden;display:flex;flex-direction:column" onclick="event.stopPropagation()">
    <div class="mh"><h3 id="credmo-title">&#128273; Credentials</h3><button class="xbtn" onclick="document.getElementById('credmo').style.display='none'">&#x2715;</button></div>
    <div id="credmo-body" style="overflow-y:auto;flex:1;padding:18px">
      <div id="credmo-status" style="margin-bottom:14px;padding:10px 14px;background:var(--bg3);border-radius:6px;font-size:13px"></div>
      <div style="font-size:12px;color:var(--mu);margin-bottom:14px">
        Per-server API key overrides the shared <code>MCP_API_KEY</code> env var.<br>
        Leave blank to fall back to the env var. Generate a JWT below or paste any bearer token.
      </div>
      <div style="background:var(--bg3);border:1px solid var(--bd);border-radius:8px;padding:14px;margin-bottom:14px">
        <div style="font-size:11px;font-weight:600;color:var(--mu);text-transform:uppercase;margin-bottom:10px;letter-spacing:.5px">&#9889; Generate JWT for this server</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end">
          <div style="flex:1;min-width:100px">
            <label style="font-size:11px;color:var(--mu);display:block;margin-bottom:4px">Subject (sub)</label>
            <input id="cg-sub" class="inp" value="fab-agent" placeholder="fab-agent" style="font-size:12px">
          </div>
          <div style="flex:1;min-width:100px">
            <label style="font-size:11px;color:var(--mu);display:block;margin-bottom:4px">Roles</label>
            <input id="cg-roles" class="inp" value="agent" placeholder="agent" style="font-size:12px">
          </div>
          <div style="width:70px">
            <label style="font-size:11px;color:var(--mu);display:block;margin-bottom:4px">Hours</label>
            <input id="cg-hours" class="inp" type="number" value="24" min="1" max="8760" style="font-size:12px">
          </div>
          <button class="btn btn-p" style="white-space:nowrap" onclick="genCredJwt()">&#128273; Generate &amp; Fill</button>
        </div>
        <div id="cg-out" style="display:none;margin-top:10px">
          <div style="font-size:11px;color:var(--gr);margin-bottom:4px">&#10003; Generated — token copied into key field below</div>
          <pre id="cg-preview" style="background:var(--bg4);border:1px solid var(--bd);border-radius:6px;padding:8px;font-size:10px;white-space:pre-wrap;word-break:break-all;max-height:80px;overflow-y:auto;color:var(--blh)"></pre>
          <div id="cg-meta" style="font-size:11px;color:var(--mu);margin-top:4px"></div>
        </div>
      </div>
      <div class="fg">
        <label class="lbl">API Key / Bearer Token (paste or generate above)</label>
        <div style="display:flex;gap:6px;align-items:stretch">
          <input id="cred-key" class="inp" type="password" placeholder="eyJ… or any bearer token" style="flex:1">
          <button class="btn btn-s" onclick="toggleCredVis()" title="Show/Hide">&#128065;</button>
        </div>
      </div>
      <div class="fg" style="margin-top:10px">
        <label class="lbl">Expires in hours (auto-set by generator, or enter manually)</label>
        <input id="cred-exp" class="inp" type="number" min="1" max="8760" placeholder="24">
      </div>
    </div>
    <div class="mf" style="gap:8px">
      <button class="btn btn-d" id="cred-clear-btn" onclick="clearCreds()">Clear Key (use env fallback)</button>
      <span style="flex:1"></span>
      <button class="btn" onclick="document.getElementById('credmo').style.display='none'">Cancel</button>
      <button class="btn btn-p" onclick="saveCreds()">Save Key</button>
    </div>
  </div>
</div>

<script>
// Catch any async unhandled rejection and show it in the login error area
window.addEventListener('unhandledrejection',function(ev){
  var el=document.getElementById('le2');
  var msg=(ev.reason&&ev.reason.message)||String(ev.reason)||'Unknown async error';
  if(el)el.textContent='JS error: '+msg;
  console.error('[hub-admin] unhandledrejection:',ev.reason);
});
console.log('[hub-admin] script loaded OK');
const BASE=location.origin;
// _tok priority: 1) cookie embedded by server at page load, 2) sessionStorage from previous tab
const _cookieTok='HUB_COOKIE_TOKEN';
let _tok=_cookieTok||sessionStorage.getItem('hub_admin_tok')||'';
if(_tok)sessionStorage.setItem('hub_admin_tok',_tok);
let _arT=null,_editMode=false,_delTarget='';
let _srvMap={};

async function api(m,p,b){
  const o={method:m,headers:{'Content-Type':'application/json','Authorization':'Bearer '+_tok}};
  if(b!==undefined)o.body=JSON.stringify(b);
  const r=await fetch(BASE+p,o);
  if(r.status===401){doLogout();throw new Error('401 Unauthorized')}
  if(!r.ok){let msg=r.statusText;try{const j=await r.json();msg=j.detail||JSON.stringify(j)}catch{}throw new Error(msg)}
  if(r.status===204)return null;
  return r.json();
}

function toggleLoginMode(e){
  e.preventDefault();
  var lf=document.getElementById('loginForm');
  var tf=document.getElementById('tokenForm');
  var tl=document.getElementById('togLink');
  if(lf.style.display==='none'){lf.style.display='';tf.style.display='none';tl.innerHTML='Use token instead &rarr;';}
  else{lf.style.display='none';tf.style.display='';tl.innerHTML='&larr; Use username/password';}
  document.getElementById('le2').textContent='';
}

function _showAdminPanel(){
  document.getElementById('loginWrap').style.display='none';
  const aw=document.getElementById('adminWrap');
  aw.style.display='flex';aw.style.flexDirection='column';
  _setUser();loadAll();
}
function _saveTok(tok){
  _tok=tok;
  sessionStorage.setItem('hub_admin_tok',tok);
  document.cookie='hub_admin_session='+tok+'; path=/; max-age='+(8*3600)+'; samesite=lax';
}
function _clearTok(){
  _tok='';
  sessionStorage.removeItem('hub_admin_tok');
  document.cookie='hub_admin_session=; path=/; max-age=0';
}

function _showErr(msg){
  var el=document.getElementById('le2');
  if(el)el.textContent='⚠ '+msg;
  console.error('[hub-admin] '+msg);
}
function _submitLogin(){
  // Wrapper so async rejection is always caught and shown in the UI
  doLogin().catch(function(e){
    _showErr(e&&e.message?e.message:'Unexpected error — see console');
    var b=document.querySelector('#loginForm button[type=submit]');
    if(b){b.disabled=false;b.textContent='Sign in';}
  });
}
async function doLogin(){
  var le2=document.getElementById('le2');
  if(le2)le2.textContent='';
  console.log('[hub-admin] doLogin called');
  var un,pw,btn;
  try{
    un=(document.getElementById('un').value||'').trim();
    pw=document.getElementById('pw').value||'';
    if(!un||!pw){_showErr('Enter username and password');return;}
    btn=document.querySelector('#loginForm button[type=submit]');
    if(btn){btn.disabled=true;btn.textContent='Signing in…';}
    console.log('[hub-admin] fetching /api/admin/login');
    const r=await fetch('/api/admin/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:un,password:pw})});
    console.log('[hub-admin] response status',r.status);
    if(!r.ok){
      let msg=r.statusText;
      try{const ej=await r.json();msg=ej.detail||msg;}catch{}
      _showErr(msg);
      return;
    }
    const d=await r.json();
    _saveTok(d.token);
    if(d.dev_mode){
      const hu=document.getElementById('hu');
      if(hu)hu.innerHTML+=' <span style="background:var(--yw);color:#000;font-size:10px;font-weight:700;padding:2px 6px;border-radius:4px;margin-left:6px">DEV MODE</span>';
    }
    _showAdminPanel();
  }catch(e){
    _showErr(e&&e.message?e.message:'Login failed — check console');
  }finally{
    if(btn){btn.disabled=false;btn.textContent='Sign in';}
  }
}

async function doLoginToken(){
  const t=(document.getElementById('ti').value||'').trim();
  document.getElementById('le2').textContent='';
  if(!t){document.getElementById('le2').textContent='Paste a token first';return;}
  _tok=t;
  try{
    await api('GET','/api/logs?n=1');
    _saveTok(t);
    _showAdminPanel();
  }catch(e){_tok='';document.getElementById('le2').textContent='&#9888; '+e.message;}
}

function _setUser(){
  try{
    const p=JSON.parse(atob(_tok.split('.')[1].replace(/-/g,'+').replace(/_/g,'/')));
    const roles=(p.roles||[]).map(r=>`<span class="bx ${r==='admin'?'badm':'bagt'}">${r}</span>`).join(' ');
    document.getElementById('hu').innerHTML=`<b>${p.sub||'user'}</b> ${roles}`;
  }catch{}
}

function doLogout(){
  _clearTok();
  if(_arT)clearInterval(_arT);
  document.getElementById('adminWrap').style.display='none';
  document.getElementById('loginWrap').style.display='flex';
  document.getElementById('ti').value='';
}

window.addEventListener('load',async()=>{
  if(_tok){
    // Token came from server cookie or sessionStorage — validate then show panel
    try{
      await api('GET','/api/logs?n=1');
      _showAdminPanel();
    }catch{
      _clearTok();
      document.getElementById('le2').textContent='Session expired — please sign in again';
    }
  }
});

document.getElementById('ti').addEventListener('keydown',e=>{if(e.key==='Enter')doLoginToken()});
document.getElementById('pw').addEventListener('keydown',e=>{if(e.key==='Enter')_submitLogin()});

function st(name){
  document.querySelectorAll('.tp').forEach(e=>e.classList.remove('act'));
  document.querySelectorAll('.tb').forEach(e=>e.classList.remove('act'));
  document.getElementById('tp-'+name).classList.add('act');
  document.querySelector(`.tb[data-t="${name}"]`).classList.add('act');
  if(name==='logs')loadLogs();
  if(name==='srv')loadSrv();
  if(name==='auth')loadAuth();
}

function loadAll(){loadDash();loadSrv();loadAuth();}

async function loadDash(){
  try{
    const h=await api('GET','/health');
    document.getElementById('dcards').innerHTML=`
      <div class="card ok"><div class="cl">Hub Status</div><div class="cv">&#x2713; Online</div>
        <div class="cs">${h.hub_name} v${h.version}</div></div>
      <div class="card info"><div class="cl">MCP Servers</div><div class="cv">${h.server_count}</div>
        <div class="cs">${h.server_ids.join(', ')||'none'}</div></div>
      <div class="card ${h.llm_enabled?'ok':'warn'}"><div class="cl">LLM Routing</div>
        <div class="cv">${h.llm_enabled?'Enabled':'Disabled'}</div>
        <div class="cs">HUB_LLM_ENABLED=${h.llm_enabled}</div></div>`;
    const d=await api('GET','/servers');
    document.getElementById('dstbl').innerHTML=d.servers.map(s=>`
      <tr><td><code>${s.id}</code></td><td>${s.name}</td>
        <td><span class="bx ${s.transport==='sse'?'bsse':'bhttp'}">${s.transport}</span></td>
        <td><span class="bx ok">active</span></td>
        <td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:monospace;font-size:12px">${s.endpoint}</td>
      </tr>`).join('');
  }catch(e){console.error('dash',e)}
}

async function loadSrv(){
  try{
    const d=await api('GET','/servers/all');
    _srvMap={};d.servers.forEach(s=>{_srvMap[s.id]=s;});
    document.getElementById('stbl').innerHTML=d.servers.map(s=>`
      <tr>
        <td><code>${s.id}</code></td><td>${s.name}</td>
        <td><span class="bx ${s.transport==='sse'?'bsse':'bhttp'}">${s.transport}</span></td>
        <td style="font-family:monospace;font-size:12px;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${s.endpoint}</td>
        <td><span class="bx ${s.is_active?'ok':'off'}">${s.is_active?'active':'inactive'}</span></td>
        <td style="white-space:nowrap;max-width:200px">
          ${s.api_key_set?'<code style="font-size:10px;color:var(--gr);word-break:break-all;user-select:text">'+s.api_key+'</code>&nbsp;<button class="btn btn-s" style="padding:1px 5px;font-size:10px" data-key="'+s.api_key+'" onclick="_cpKey(this)">Copy</button>':'<span style="color:var(--mu)">env</span>'}
        </td>
        <td style="max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${s.capability||'—'}</td>
        <td style="text-align:right;white-space:nowrap">
          <button class="btn btn-s" onclick="testS('${s.id}')">&#128268; Test</button>
          <button class="btn btn-s" onclick="showTools('${s.id}')">&#128295; Tools</button>
          <button class="btn btn-s" onclick="showCreds('${s.id}')">&#128273; Key</button>
          <button class="btn btn-s" onclick="showHistory('${s.id}')">&#128203; History</button>
          <button class="btn btn-s" onclick="showDetails('${s.id}')">&#9432; Details</button>
          <button class="btn btn-s" onclick="openM('${s.id}')">Edit</button>
          <button class="btn btn-s btn-d" onclick="confDel('${s.id}')">Delete</button>
        </td>
      </tr>`).join('');
    document.getElementById('sci').textContent=`${d.servers.length} servers`;
  }catch(e){console.error('srv',e)}
}

async function rfCache(){
  try{await api('POST','/api/hub/refresh');loadSrv();loadDash();}
  catch(e){alert('Cache refresh failed: '+e.message)}
}

async function rotateKeys(){
  if(!confirm('Generate NEW unique keys for ALL MCP servers?\\n\\nAfter clicking OK, restart all MCP servers so they reload their new key from MySQL.'))return;
  try{
    const d=await api('POST','/api/hub/rotate-server-keys');
    alert(`Keys rotated for ${d.rotated} server(s).\\n\\nIMPORTANT: Restart all MCP servers for the new keys to take effect.\\n\\n${d.servers.map(s=>s.id+': '+s.key_hint).join('\\n')}`);
    loadSrv();loadDash();
  }catch(e){alert('Rotate failed: '+e.message)}
}

let _curTestId='';
async function testS(id){
  _curTestId=id||_curTestId;
  document.getElementById('tmo-title').textContent='Test — '+_curTestId;
  document.getElementById('tmo-body').innerHTML='<div style="padding:18px;color:var(--mu)">Testing connectivity…</div>';
  document.getElementById('testmo').style.display='flex';
  try{
    const d=await api('POST','/servers/'+encodeURIComponent(_curTestId)+'/test');
    const req=d.request||{};
    document.getElementById('tmo-body').innerHTML=_buildResultHtml(d,req,'tmo');
  }catch(e){
    document.getElementById('tmo-body').innerHTML='<div style="padding:18px;color:var(--rd)">Error: '+esc(e.message)+'</div>';
  }
}
function closeTmo(e){
  if(e&&e.target!==document.getElementById('testmo'))return;
  closeTmoStop();
}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function _tokHtml(v){
  if(!v||!v.startsWith('Bearer '))return '<code style="user-select:text">'+esc(v)+'</code>';
  const tok=v.slice(7);
  const short=tok.length>40?tok.slice(0,40)+'…':tok;
  // Use a data attribute so the token (which may contain any base64url chars) is
  // never embedded raw inside an onclick="..." string literal.
  return `<span style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">`
    +`<code style="user-select:text;word-break:break-all">Bearer `
    +`<span class="tok-s">${esc(short)}</span>`
    +`<span class="tok-f" style="display:none">${esc(tok)}</span></code>`
    +` <a href="#" style="font-size:11px;color:var(--acc);white-space:nowrap" `
    +`onclick="event.preventDefault();var p=this.closest('span');p.querySelector('.tok-s').style.display='none';p.querySelector('.tok-f').style.display='';this.textContent='hide';this.onclick=function(ev){ev.preventDefault();p.querySelector('.tok-s').style.display='';p.querySelector('.tok-f').style.display='none';this.textContent='view';}">view</a>`
    +` <button class="btn btn-s" style="font-size:10px;padding:1px 6px" data-tok="${esc(tok)}" `
    +`onclick="_cpTok(this)">Copy</button>`
    +`</span>`;
}
function _cpTok(btn){
  navigator.clipboard.writeText(btn.dataset.tok||'');
  btn.textContent='Copied!';setTimeout(()=>btn.textContent='Copy',1500);
}
function _cpKey(btn){
  navigator.clipboard.writeText(btn.dataset.key||'');
  btn.textContent='✓';setTimeout(()=>btn.textContent='Copy',1500);
}
function _hdrTable(headers){
  return Object.entries(headers||{}).map(([k,v])=>`<tr><td style="color:var(--mu);padding:3px 0;vertical-align:top;white-space:nowrap;width:160px">${esc(k)}</td><td style="word-break:break-all;padding-left:8px;user-select:text">${k.toLowerCase()==='authorization'?_tokHtml(v):'<code>'+esc(v)+'</code>'}</td></tr>`).join('');
}
function _cp(txt,btn){navigator.clipboard.writeText(txt);btn.textContent='Copied!';setTimeout(()=>btn.textContent='Copy',1500);}
function _buildResultHtml(d,req,pfx){
  const ok=d.ok;
  const curlVal=req.curl||'';
  const httpRaw=req.http_raw||'';
  const headersJson=req.headers_json||JSON.stringify(req.headers||{},null,2);
  const bodyJson=req.body_json||JSON.stringify(req.body||{},null,2);
  const respJson=d.response_body?JSON.stringify(d.response_body,null,2):'';
  const ksColor=d.key_source==='hub-minted-jwt'?'var(--gr)':d.key_source==='per-server-db'?'var(--yw)':'var(--mu)';
  // Extract the full bearer token from the request headers so we can show it prominently
  const _authHdr=(req.headers||{})['Authorization']||(req.headers||{})['authorization']||'';
  const _bearerTok=_authHdr.startsWith('Bearer ')?_authHdr.slice(7):'';
  // Store curl in a hidden textarea for the modal footer "Copy curl" button
  const curlStore=`<textarea id="${pfx}-curl-store" style="display:none">${curlVal.replace(/</g,'&lt;')}</textarea>`;
  return curlStore+`
    <div style="padding:12px 18px;border-bottom:1px solid var(--bd);display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap">
      <div style="display:flex;gap:12px;align-items:center">
        <span style="font-size:20px">${ok?'✅':'❌'}</span>
        <div>
          <div style="font-weight:600;font-size:14px">${ok?'Reachable':'Unreachable'}</div>
          <div style="font-size:12px;color:var(--mu)">
            ${d.status_text||''} ${d.error?`<span style="color:var(--rd)">${esc(d.error)}</span>`:''}
            &nbsp;·&nbsp; ${d.latency_ms}ms
            &nbsp;·&nbsp; Auth: ${d.auth_used?'<span style="color:var(--gr)">sent</span>':'<span style="color:var(--rd)">not configured</span>'}
            ${d.key_source?`&nbsp;·&nbsp; Key: <span style="color:${ksColor}">${esc(d.key_source)}</span>`:''}
            ${d.transport?`&nbsp;·&nbsp; Transport: <code style="font-size:11px">${esc(d.transport)}</code>`:''}
          </div>
        </div>
      </div>
      <button class="btn btn-s" onclick="${pfx==='tmo'?'testS()':'showTools(_curToolsId)'}">&#8635; Re-run</button>
    </div>
    ${_bearerTok?`<div style="padding:10px 18px;border-bottom:1px solid var(--bd);background:var(--bg3)">
      <div style="font-size:11px;font-weight:600;color:var(--mu);text-transform:uppercase;margin-bottom:6px;display:flex;align-items:center;gap:8px">
        &#128273; Bearer Token Used &nbsp;
        <button class="btn btn-s" style="font-size:10px;padding:1px 7px" data-tok="${esc(_bearerTok)}" onclick="_cpTok(this)">Copy</button>
      </div>
      <code style="font-size:10px;word-break:break-all;user-select:text;color:var(--blh);display:block;line-height:1.5">${esc(_bearerTok)}</code>
    </div>`:''}
    <div style="padding:12px 18px;border-bottom:1px solid var(--bd)">
      <div style="font-size:11px;font-weight:600;color:var(--mu);text-transform:uppercase;margin-bottom:8px">Request</div>
      <table style="width:100%;border-collapse:collapse;font-size:12px;margin-bottom:8px">
        <tr><td style="color:var(--mu);padding:3px 0;width:80px">Method</td><td><code>${req.method||'POST'}</code></td></tr>
        <tr><td style="color:var(--mu);padding:3px 0">URL</td><td style="word-break:break-all;user-select:text"><code>${esc(req.url||d.endpoint||'')}</code></td></tr>
        ${_hdrTable(req.headers)}
        ${req.body?`<tr><td style="color:var(--mu);padding:3px 0;vertical-align:top">Body</td><td style="word-break:break-all;padding-left:0;user-select:text"><code>${esc(JSON.stringify(req.body))}</code></td></tr>`:''}
      </table>
      <div style="display:flex;gap:6px;margin-bottom:8px;flex-wrap:wrap">
        <button class="btn btn-s" onclick="_showFmt('${pfx}','curl')">curl</button>
        <button class="btn btn-s" onclick="_showFmt('${pfx}','http')">HTTP raw</button>
        <button class="btn btn-s" onclick="_showFmt('${pfx}','json')">Headers JSON</button>
      </div>
      <div id="${pfx}-fmt-curl">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
          <span style="font-size:11px;color:var(--mu)">curl</span>
          <button class="btn btn-s" onclick="_cp(document.getElementById('${pfx}-pre-curl').textContent,this)">Copy</button>
        </div>
        <pre id="${pfx}-pre-curl" style="background:var(--bg3);border:1px solid var(--bd);border-radius:6px;padding:10px;font-size:11px;white-space:pre-wrap;word-break:break-all;color:var(--gr);margin:0;user-select:text">${curlVal.replace(/</g,'&lt;')}</pre>
      </div>
      <div id="${pfx}-fmt-http" style="display:none">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
          <span style="font-size:11px;color:var(--mu)">Raw HTTP/1.1</span>
          <button class="btn btn-s" onclick="_cp(document.getElementById('${pfx}-pre-http').textContent,this)">Copy</button>
        </div>
        <pre id="${pfx}-pre-http" style="background:var(--bg3);border:1px solid var(--bd);border-radius:6px;padding:10px;font-size:11px;white-space:pre-wrap;word-break:break-all;color:var(--gr);margin:0;user-select:text">${httpRaw.replace(/</g,'&lt;')}</pre>
      </div>
      <div id="${pfx}-fmt-json" style="display:none">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
          <span style="font-size:11px;color:var(--mu)">Headers (JSON) + Body</span>
          <button class="btn btn-s" onclick="_cp(document.getElementById('${pfx}-pre-json').textContent,this)">Copy</button>
        </div>
        <pre id="${pfx}-pre-json" style="background:var(--bg3);border:1px solid var(--bd);border-radius:6px;padding:10px;font-size:11px;white-space:pre-wrap;word-break:break-all;color:var(--gr);margin:0;user-select:text">${('// Headers\\n'+headersJson+(bodyJson?'\\n\\n// Body\\n'+bodyJson:'')).replace(/</g,'&lt;')}</pre>
      </div>
    </div>
    ${respJson?`<div style="padding:12px 18px;border-bottom:1px solid var(--bd)">
      <div style="font-size:11px;font-weight:600;color:var(--mu);text-transform:uppercase;margin-bottom:6px;display:flex;align-items:center;gap:8px">
        Response <button class="btn btn-s" onclick="_cp(document.getElementById('${pfx}-resp').textContent,this)">Copy</button>
      </div>
      <pre id="${pfx}-resp" style="background:var(--bg3);border:1px solid var(--bd);border-radius:6px;padding:10px;font-size:11px;white-space:pre-wrap;word-break:break-all;color:var(--gr);margin:0;max-height:200px;overflow-y:auto;user-select:text">${respJson.replace(/</g,'&lt;')}</pre>
    </div>`:''}
    <details style="border-top:1px solid var(--bd)">
      <summary style="padding:10px 18px;cursor:pointer;font-size:12px;color:var(--mu);user-select:none;list-style:none">&#9656; Edit headers &amp; body — re-run</summary>
      <div style="padding:10px 18px">
        <div style="font-size:11px;color:var(--mu);margin-bottom:8px">Edit headers and/or body then click Re-run with Edit. Only affects this one-off call — original defaults are restored on Reset.</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:8px">
          <div>
            <label style="font-size:11px;font-weight:600;color:var(--mu);text-transform:uppercase;display:block;margin-bottom:4px">Headers (JSON object, overrides defaults)</label>
            <textarea id="${pfx}-edit-hdrs" spellcheck="false" style="width:100%;height:110px;font-family:monospace;font-size:11px;background:var(--bg3);color:var(--fg);border:1px solid var(--bd);border-radius:6px;padding:8px;box-sizing:border-box;resize:vertical">${esc(headersJson)}</textarea>
          </div>
          <div>
            <label style="font-size:11px;font-weight:600;color:var(--mu);text-transform:uppercase;display:block;margin-bottom:4px">Body (JSON-RPC request)</label>
            <textarea id="${pfx}-edit-body" spellcheck="false" style="width:100%;height:110px;font-family:monospace;font-size:12px;background:var(--bg3);color:var(--fg);border:1px solid var(--bd);border-radius:6px;padding:8px;box-sizing:border-box;resize:vertical">${bodyJson.replace(/</g,'&lt;')}</textarea>
          </div>
        </div>
        <div style="display:flex;gap:6px">
          <button class="btn btn-s btn-p" onclick="_rerunEdited('${pfx}')">&#9654; Re-run with Edit</button>
          <button class="btn btn-s" onclick="${pfx==='tmo'?'testS()':'showTools(_curToolsId)'}">&#8635; Reset</button>
        </div>
        <div id="${pfx}-edit-result" style="margin-top:8px;font-size:12px"></div>
      </div>
    </details>`;
}
function _showFmt(pfx,tab){
  ['curl','http','json'].forEach(t=>document.getElementById(pfx+'-fmt-'+t).style.display=t===tab?'':'none');
}

// ---- Test modal: auto-refresh + copy curl + edit-and-rerun ----
let _tmo_autoTimer=null;
function _tmo_toggleAuto(){
  const btn=document.getElementById('tmo-auto-btn');
  if(_tmo_autoTimer){
    clearInterval(_tmo_autoTimer);_tmo_autoTimer=null;
    if(btn)btn.textContent='\\u25B6 Auto-refresh off';
  } else {
    if(btn)btn.textContent='\\u23F8 Auto-refresh 5s (on)';
    _tmo_autoTimer=setInterval(()=>testS(),5000);
  }
}
function closeTmoStop(){
  if(_tmo_autoTimer){clearInterval(_tmo_autoTimer);_tmo_autoTimer=null;}
  const btn=document.getElementById('tmo-auto-btn');
  if(btn)btn.textContent='\\u25B6 Auto-refresh off';
  document.getElementById('testmo').style.display='none';
}
function _tmo_copyCurl(btn){
  const el=document.getElementById('tmo-curl-store');
  if(el){navigator.clipboard.writeText(el.value);btn.textContent='Copied!';setTimeout(()=>btn.textContent='\\uD83D\\uDCCB Copy curl',1500);}
}
// ---- Tools modal: copy curl ----
function _tls_copyCurl(btn){
  const el=document.getElementById('tls-curl-store');
  if(el){navigator.clipboard.writeText(el.value);btn.textContent='Copied!';setTimeout(()=>btn.textContent='\\uD83D\\uDCCB Copy curl',1500);}
}
// ---- Edit headers+body re-run: sends to POST /servers/{id}/probe ----
async function _rerunEdited(pfx){
  const taBody=document.getElementById(pfx+'-edit-body');
  const taHdrs=document.getElementById(pfx+'-edit-hdrs');
  const out=document.getElementById(pfx+'-edit-result');
  if(!taBody||!out)return;
  let body,hdrs;
  try{body=JSON.parse(taBody.value);}catch(e){out.innerHTML='<span style="color:var(--rd)">Body JSON invalid: '+esc(e.message)+'</span>';return;}
  if(taHdrs&&taHdrs.value.trim()){
    try{hdrs=JSON.parse(taHdrs.value);}catch(e){out.innerHTML='<span style="color:var(--rd)">Headers JSON invalid: '+esc(e.message)+'</span>';return;}
  }
  out.innerHTML='<span style="color:var(--mu)">Running…</span>';
  const serverId=pfx==='tmo'?_curTestId:_curToolsId;
  const payload={custom_body:body};
  if(hdrs)payload.custom_headers=hdrs;
  try{
    const d=await api('POST','/servers/'+encodeURIComponent(serverId)+'/probe',payload);
    const ok=d.ok;
    // Show full bearer token from the probe response request detail
    const _respTok=((d.request||{}).headers||{})['Authorization']||'';
    const _tokPart=_respTok.startsWith('Bearer ')?_respTok.slice(7):'';
    out.innerHTML=(ok?'<span style="color:var(--gr)">✅ OK</span>':'<span style="color:var(--rd)">❌ '+esc(d.error||'failed')+'</span>')
      +' &nbsp;·&nbsp; '+d.latency_ms+'ms'
      +(_tokPart?'<br><div style="font-size:10px;color:var(--mu);margin-top:4px">Token: <code style="user-select:text;word-break:break-all">'+esc(_tokPart)+'</code>'
        +' <button class="btn btn-s" style="font-size:9px;padding:0 5px" data-tok="'+esc(_tokPart)+'" onclick="_cpTok(this)">Copy</button></div>':'')
      +(d.response_body?'<pre style="font-size:11px;background:var(--bg3);border:1px solid var(--bd);border-radius:4px;padding:8px;white-space:pre-wrap;user-select:text;max-height:160px;overflow-y:auto;margin-top:6px">'+JSON.stringify(d.response_body,null,2).replace(/</g,'&lt;')+'</pre>':'');
  }catch(e){out.innerHTML='<span style="color:var(--rd)">'+esc(e.message)+'</span>';}
}

function openM(serverId){
  _editMode=!!serverId;
  const s=serverId?(_srvMap[serverId]||null):null;
  document.getElementById('mt').textContent=s?'Edit MCP Server':'Add MCP Server';
  document.getElementById('fid').disabled=!!s;
  document.getElementById('fid').value=s?s.id:'';
  document.getElementById('fnm').value=s?s.name:'';
  document.getElementById('fep').value=s?s.endpoint:'';
  document.getElementById('ftr').value=s?s.transport:'sse';
  document.getElementById('fac').checked=s?(s.is_active!==false):true;
  document.getElementById('fca').value=s?s.capability||'':'';
  document.getElementById('fds').value=s?s.description||'':'';
  document.getElementById('fsk').value=s?(Array.isArray(s.skills)?s.skills.join(', '):''):'';
  document.getElementById('fex').value=s?(Array.isArray(s.examples)?s.examples.join(', '):''):'';
  document.getElementById('fsc').value=s?s.start_cmd||'':'';
  document.getElementById('mo').style.display='flex';
}

function closeM(e){
  if(e&&e.target!==document.getElementById('mo'))return;
  document.getElementById('mo').style.display='none';
}

async function saveS(){
  const id=document.getElementById('fid').value.trim();
  if(!id){alert('Server ID is required');return;}
  const ep=document.getElementById('fep').value.trim();
  if(!ep){alert('Endpoint URL is required');return;}
  const body={
    id,name:document.getElementById('fnm').value.trim()||id,endpoint:ep,
    transport:document.getElementById('ftr').value,
    is_active:document.getElementById('fac').checked,
    capability:document.getElementById('fca').value.trim(),
    description:document.getElementById('fds').value.trim(),
    skills:document.getElementById('fsk').value.split(',').map(s=>s.trim()).filter(Boolean),
    examples:document.getElementById('fex').value.split(',').map(s=>s.trim()).filter(Boolean),
    start_cmd:document.getElementById('fsc').value.trim(),
  };
  try{
    if(_editMode)await api('PUT','/servers/'+id,body);
    else await api('POST','/servers',body);
    document.getElementById('mo').style.display='none';
    loadSrv();loadDash();
  }catch(e){alert('Save failed: '+e.message)}
}

function confDel(id){
  _delTarget=id;
  document.getElementById('cm').innerHTML=`Delete server <b>${id}</b>? This cannot be undone.`;
  document.getElementById('co').style.display='flex';
  document.getElementById('cok').onclick=doDel;
}
function closeC(){document.getElementById('co').style.display='none';}
async function doDel(){
  try{await api('DELETE','/servers/'+_delTarget);closeC();loadSrv();loadDash();}
  catch(e){alert('Delete failed: '+e.message)}
}

async function loadLogs(){
  const t=document.getElementById('ltf').value;
  const n=document.getElementById('lcf').value;
  try{
    const d=await api('GET','/api/logs?n='+n+(t?'&event_type='+t:''));
    document.getElementById('lm').textContent=d.returned+' events';
    const c=document.getElementById('lc2');
    if(!d.events||!d.events.length){
      c.innerHTML='<div style="padding:18px;color:var(--mu);text-align:center">No events yet</div>';return;
    }
    c.innerHTML=[...d.events].reverse().map(ev=>{
      const ts=new Date(ev.ts*1000).toLocaleTimeString('en-US',{hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'});
      let body='';
      if(ev.type==='auth'){
        const c2=ev.valid?'var(--gr)':'var(--rd)';
        body=`<span style="color:${c2}">${ev.valid?'&#10003;':'&#10007;'}</span> `+
          `<b>${ev.sub||'?'}</b> roles=[${(ev.roles||[]).join(',')}] type=${ev.token_type||'?'}`+
          ` &rarr; ${ev.method||''} ${ev.endpoint||''}`;
      }else if(ev.type==='routing'){
        body=`<b>${(ev.server_ids||[]).join(', ')||'none'}</b> via ${ev.method||'?'} &mdash; ${(ev.reason||'').substring(0,80)}`;
      }else if(ev.type==='request'){
        const c3=ev.status>=400?'var(--rd)':ev.status>=300?'var(--yw)':'var(--gr)';
        body=`<span style="color:${c3}">${ev.status}</span> <b>${ev.method} ${ev.path}</b> ${ev.latency_ms||0}ms`;
      }else if(ev.type==='admin'){
        body=`<b>${ev.action||'?'}</b> ${ev.server_id?'server_id='+ev.server_id:''}`;
      }else if(ev.type==='request_detail'){
        const reqB=ev.request_body?JSON.stringify(ev.request_body).substring(0,80):'';
        const respB=ev.response_body?JSON.stringify(ev.response_body).substring(0,80):'';
        body=`<b>${ev.method||'POST'} ${ev.endpoint||ev.path||'?'}</b>`
          +(ev.auth_sub?` auth=<b>${ev.auth_sub}</b> [${(ev.auth_roles||[]).join(',')}]`:'')
          +(reqB?` req=${reqB}`:'')
          +(respB?` resp=${respB}`:'');
      }else{
        body=Object.entries(ev).filter(([k])=>k!=='type'&&k!=='ts')
          .map(([k,v])=>`${k}=<b>${typeof v==='object'?JSON.stringify(v):v}</b>`).join(' ');
      }
      return `<div class="le"><span class="lts">${ts}</span><span class="lty ${ev.type}">${ev.type}</span><span class="lb">${body}</span></div>`;
    }).join('');
  }catch(e){console.error('logs',e)}
}

function togAR(){
  if(document.getElementById('ar').checked){_arT=setInterval(loadLogs,5000);}
  else{if(_arT)clearInterval(_arT);_arT=null;}
}

async function loadAuth(){
  try{
    const h=await api('GET','/health');
    document.getElementById('acv').innerHTML=`
      <table class="kv">
        <tr><td>Hub</td><td><b>${h.hub_name}</b> v${h.version}</td></tr>
        <tr><td>LLM Routing</td><td>${h.llm_enabled?'<span class="bx ok">enabled</span>':'<span class="bx bdev">disabled</span>'}</td></tr>
        <tr><td>Server Count</td><td><b>${h.server_count}</b> active servers</td></tr>
        <tr><td>Your Identity</td><td id="myId"></td></tr>
      </table>`;
    try{
      const p=JSON.parse(atob(_tok.split('.')[1].replace(/-/g,'+').replace(/_/g,'/')));
      const exp=p.exp?new Date(p.exp*1000).toLocaleString():'n/a';
      document.getElementById('myId').innerHTML=
        `sub=<b>${p.sub}</b>  roles=[${(p.roles||[]).join(',')}]  exp=${exp}`;
    }catch{document.getElementById('myId').textContent='(API key — no JWT claims)';}
  }catch(e){console.error('auth',e)}
  loadAuthStatus();
}
async function loadAuthStatus(){
  const el=document.getElementById('auth-status-panel');
  if(!el)return;
  try{
    const r=await fetch(BASE+'/api/auth/status',{headers:{'Authorization':'Bearer '+_tok,'Content-Type':'application/json'}});
    if(!r.ok){
      el.innerHTML='<div style="color:var(--mu);font-size:12px;padding:8px">Auth status unavailable ('+r.status+')</div>';
      return;
    }
    const s=await r.json();
    const chk='<span style="color:var(--gr);font-weight:700">&#10003;</span>';
    const warn='<span style="color:var(--rd);font-weight:700">&#9888;</span>';
    const yn=(v,hint)=>v?chk+(hint?` <code style="font-size:11px;color:var(--mu)">${hint}</code>`:''):warn+' NOT SET';
    // Layer rows
    const layers=[
      ['1','Browser &#8594; Chat',`<code>JWT_SECRET</code>`,yn(s.jwt_secret,s.jwt_secret_hint),`Signs user session JWTs (8h). Both chat_server and hub use this.`],
      ['2','Agent &#8594; Hub',`<code>HUB_API_KEY</code>`,yn(s.hub_api_key,s.hub_api_key_hint),`Bearer token sent by agent.py to /discover. Hub validates with JWT_SECRET.`],
      ['3a','Agent &#8594; MCP (per-server)',`MySQL <code>mcp_servers.api_key</code>`,`<span style="color:var(--bl);font-weight:600">${s.servers_with_key}/${s.active_servers}</span> servers`,`Unique key per server. Agent uses it from /discover response. Takes priority over MCP_API_KEY.`],
      ['3b','Agent &#8594; MCP (fallback)',`<code>MCP_API_KEY</code>`,yn(s.mcp_api_key,s.mcp_api_key_hint),`Shared fallback when no per-server key. Roles granted: <code>${s.mcp_api_key_roles||'agent'}</code>`],
      ['4','MCP tool RBAC',`<code>MCP_API_KEY_ROLES</code>`,`<code style="color:var(--yw)">${s.mcp_api_key_roles||'agent'}</code>`,`Roles the agent gets when it authenticates. Needs "admin" to call admin-only tools.`],
    ];
    // Per-server key table
    const srvRows=(s.servers||[]).map(sv=>`
      <tr>
        <td><code>${sv.id}</code></td>
        <td style="max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${sv.name}</td>
        <td>${sv.key_set?chk+' <span style="color:var(--gr)">set</span>':warn+' not set'}</td>
        <td><code style="font-size:11px;color:var(--mu)">${sv.key_hint}</code></td>
        <td>${sv.key_unique?'<span class="bx ok" style="font-size:10px">unique</span>':'<span class="bx bagt" style="font-size:10px">shared</span>'}</td>
      </tr>`).join('');
    el.innerHTML=`
      <table class="tbl" style="font-size:12px;margin-bottom:16px">
        <thead><tr><th>#</th><th>Flow</th><th>Variable</th><th>Status / Value</th><th>Purpose</th></tr></thead>
        <tbody>${layers.map(([n,f,v,st,p])=>`<tr><td style="color:var(--mu)">${n}</td><td>${f}</td><td>${v}</td><td>${st}</td><td style="color:var(--mu);font-size:11px">${p}</td></tr>`).join('')}</tbody>
      </table>
      <div style="margin-bottom:8px;font-size:12px;font-weight:600;color:var(--tx)">Per-Server Keys (MySQL <code>mcp_servers.api_key</code>)</div>
      <table class="tbl" style="font-size:12px;margin-bottom:16px">
        <thead><tr><th>Server ID</th><th>Name</th><th>Key Status</th><th>Key Hint (first 10 chars)</th><th>Type</th></tr></thead>
        <tbody>${srvRows||'<tr><td colspan="5" style="color:var(--mu)">No servers found</td></tr>'}</tbody>
      </table>
      <div style="font-size:12px;color:var(--mu);padding:8px 12px;background:var(--bg3);border-radius:6px;border-left:3px solid var(--yw)">
        <b style="color:var(--tx)">Key types:</b>
        <span class="bx ok" style="font-size:10px">unique</span> = starts with <code>srv-</code>, generated specifically for this server &nbsp;|&nbsp;
        <span class="bx bagt" style="font-size:10px">shared</span> = same value as MCP_API_KEY or old <code>mcp-</code> prefix.<br>
        Use <b>&#128273; Rotate All Keys</b> button on Servers tab to generate unique keys for all servers.
        Then restart all MCP servers (they cache their key at startup).
      </div>`;
  }catch(e){if(el)el.innerHTML='<div style="color:var(--rd);font-size:12px;padding:8px">Error loading auth status: '+e.message+'</div>';}
}

async function genTok(){
  const sub=document.getElementById('ts').value.trim()||'fab-agent';
  const roles=document.getElementById('tr2').value.split(',').map(s=>s.trim()).filter(Boolean);
  const hours=parseInt(document.getElementById('th').value)||24;
  try{
    const r=await api('POST','/api/auth/token',{sub,roles,hours});
    document.getElementById('tout').style.display='block';
    document.getElementById('tout').textContent=r.token;
    const tc=document.getElementById('tcopy');
    tc.style.display='flex';
    document.getElementById('ti2').textContent=
      `sub=${sub}  roles=[${roles.join(',')}]  expires: ${new Date(r.expires_at*1000).toLocaleString()}`;
  }catch(e){alert('Token generation failed: '+e.message)}
}

async function cpTok(){
  await navigator.clipboard.writeText(document.getElementById('tout').textContent);
  const b=event.target;b.textContent='&#10003; Copied!';
  setTimeout(()=>b.innerHTML='&#128203; Copy',2000);
}

async function showHistory(id){
  document.getElementById('chmt').textContent='Change History — '+id;
  document.getElementById('chmc').innerHTML='<div style="padding:18px;color:var(--mu)">Loading…</div>';
  document.getElementById('chmo').style.display='flex';
  try{
    const d=await api('GET','/api/servers/changelog?server_id='+encodeURIComponent(id)+'&limit=50');
    const rows=d.changelog||[];
    if(!rows.length){
      document.getElementById('chmc').innerHTML='<div style="padding:18px;color:var(--mu);text-align:center">No history yet for this server.</div>';
      return;
    }
    const aColor={create:'ok',delete:'off',update:'bdev'};
    document.getElementById('chmc').innerHTML=
      '<table class="tbl"><thead><tr><th>#</th><th>Action</th><th>Changed By</th><th>Changed At</th><th>Details</th></tr></thead><tbody>'+
      rows.map(r=>`<tr>
        <td style="color:var(--mu);font-size:11px">${r.id}</td>
        <td><span class="bx ${aColor[r.action]||'bdev'}">${r.action}</span></td>
        <td>${r.changed_by||'—'}</td>
        <td style="white-space:nowrap;font-size:12px">${new Date(r.changed_at).toLocaleString()}</td>
        <td style="max-width:240px">
          ${r.before_state?'<details><summary style="cursor:pointer;font-size:11px;color:var(--mu)">before</summary><pre style="font-size:10px;overflow:auto;max-height:120px;color:var(--mu);white-space:pre-wrap">'+JSON.stringify(r.before_state,null,2)+'</pre></details>':''}
          ${r.after_state?'<details><summary style="cursor:pointer;font-size:11px;color:var(--mu)">after</summary><pre style="font-size:10px;overflow:auto;max-height:120px;color:var(--mu);white-space:pre-wrap">'+JSON.stringify(r.after_state,null,2)+'</pre></details>':''}
        </td></tr>`).join('')+
      '</tbody></table>';
  }catch(e){document.getElementById('chmc').innerHTML='<div style="padding:18px;color:var(--rd)">Error: '+e.message+'</div>';}
}
function closeCh(e){
  if(e&&e.target!==document.getElementById('chmo'))return;
  document.getElementById('chmo').style.display='none';
}

let _curToolsId='';
async function showTools(id){
  _curToolsId=id||_curToolsId;
  document.getElementById('tlsmt').textContent='Tools — '+_curToolsId;
  document.getElementById('tlsmc').innerHTML='<div style="padding:18px;color:var(--mu)">Fetching tools…</div>';
  document.getElementById('toolsmo').style.display='flex';
  try{
    const d=await api('GET','/servers/'+encodeURIComponent(_curToolsId)+'/tools');
    const req=d.request||{};
    const tools=d.tools||[];
    const ksColor=d.key_source==='hub-minted-jwt'?'var(--gr)':d.key_source==='per-server-db'?'var(--yw)':'var(--mu)';
    let html='';
    if(!d.ok){
      html=`<div style="padding:12px 18px;display:flex;gap:10px;align-items:center;border-bottom:1px solid var(--bd)">`
        +`<span style="font-size:20px">❌</span>`
        +`<div><div style="font-weight:600;font-size:13px;color:var(--rd)">${esc(d.error||'Probe failed')}</div>`
        +`<div style="font-size:12px;color:var(--mu)">${d.latency_ms}ms &nbsp;·&nbsp; `
        +`Key: <span style="color:${ksColor}">${esc(d.key_source||'?')}</span>`
        +` &nbsp;·&nbsp; Transport: <code style="font-size:11px">${esc(d.transport||'?')}</code></div></div></div>`;
    } else if(tools.length){
      html=`<div style="padding:10px 18px;font-size:12px;color:var(--mu);border-bottom:1px solid var(--bd)">`
        +`✅ ${d.count||0} tools &nbsp;·&nbsp; ${d.latency_ms}ms &nbsp;·&nbsp; `
        +`Key: <span style="color:${ksColor}">${esc(d.key_source||'?')}</span>`
        +` &nbsp;·&nbsp; Transport: <code style="font-size:11px">${esc(d.transport||'?')}</code></div>`
        +`<table class="tbl"><thead><tr><th>Tool</th><th>Description</th><th>Input Schema</th><th>Output Schema</th></tr></thead><tbody>`
        +tools.map(t=>{const iS=t.inputSchema?JSON.stringify(t.inputSchema,null,2):'—';const oS=t.outputSchema?JSON.stringify(t.outputSchema,null,2):'—';return`<tr><td><code style="user-select:text">${esc(t.name||'')}</code></td><td style="color:var(--mu);font-size:12px;user-select:text">${esc(t.description||'—')}</td><td><pre style="font-size:11px;max-width:260px;max-height:120px;overflow:auto;white-space:pre-wrap;user-select:text;margin:0;padding:4px;border-radius:4px;background:rgba(0,0,0,.04)">${esc(iS)}</pre></td><td><pre style="font-size:11px;max-width:260px;max-height:120px;overflow:auto;white-space:pre-wrap;user-select:text;margin:0;padding:4px;border-radius:4px;background:rgba(0,0,0,.04)">${esc(oS)}</pre></td></tr>`;}).join('')
        +`</tbody></table>`;
    } else {
      html=`<div style="padding:12px 18px;font-size:13px;color:var(--mu)">✅ Server online · no tools returned · ${d.latency_ms}ms</div>`;
    }
    html+=`<details style="border-top:1px solid var(--bd)">
      <summary style="padding:10px 18px;cursor:pointer;font-size:12px;color:var(--mu);user-select:none;list-style:none">&#9656; Request details, curl &amp; re-run</summary>
      <div id="tls-detail">${_buildResultHtml(d,req,'tls')}</div>
    </details>`;
    document.getElementById('tlsmc').innerHTML=html;
  }catch(e){document.getElementById('tlsmc').innerHTML='<div style="padding:18px;color:var(--rd)">Error: '+esc(e.message)+'</div>';}
}
function closeTls(e){
  if(e&&e.target!==document.getElementById('toolsmo'))return;
  document.getElementById('toolsmo').style.display='none';
}

function copyDetPre(preId,btn){
  navigator.clipboard.writeText(document.getElementById(preId).textContent);
  btn.textContent='Copied!';setTimeout(()=>btn.textContent='Copy',1500);
}
async function showDetails(id){
  const s=_srvMap[id];
  if(!s){alert('Server not found');return;}
  document.getElementById('detmo-title').textContent='Details — '+id;
  const ep=s.endpoint||'';
  const NL="\\n", BS="\\\\";
  const curlTest='curl -X POST '+JSON.stringify(ep)+' '+BS+NL+
    '  -H "Content-Type: application/json" '+BS+NL+
    '  -H "Accept: application/json, text/event-stream" '+BS+NL+
    '  -H "Authorization: Bearer <MCP_API_KEY>" '+BS+NL+
    "  -d '"+JSON.stringify({jsonrpc:'2.0',id:'test',method:'ping',params:{}})+"'";
  const curlTools='curl -X POST '+JSON.stringify(ep)+' '+BS+NL+
    '  -H "Content-Type: application/json" '+BS+NL+
    '  -H "Accept: application/json, text/event-stream" '+BS+NL+
    '  -H "Authorization: Bearer <MCP_API_KEY>" '+BS+NL+
    "  -d '"+JSON.stringify({jsonrpc:'2.0',id:1,method:'tools/list',params:{}})+"'";
  const headersBlock='{'+NL+
    '  "Content-Type": "application/json",'+NL+
    '  "Accept": "application/json, text/event-stream",'+NL+
    '  "Authorization": "Bearer <MCP_API_KEY>"'+NL+
    '}';
  const skills=(s.skills||[]).map(sk=>'<span class="bx bhttp">'+sk+'</span>').join(' ')||'—';
  const examples=(s.examples||[]).map(ex=>'<div style="font-size:12px;color:var(--mu)">'+ex+'</div>').join('')||'—';
  document.getElementById('detmo-body').innerHTML=
    '<table class="kv" style="margin:0">'+
    '<tr><td>ID</td><td><code>'+s.id+'</code></td></tr>'+
    '<tr><td>Name</td><td>'+s.name+'</td></tr>'+
    '<tr><td>Endpoint</td><td><code>'+ep+'</code></td></tr>'+
    '<tr><td>Transport</td><td><span class="bx '+(s.transport==='sse'?'bsse':'bhttp')+'">'+s.transport+'</span></td></tr>'+
    '<tr><td>Status</td><td><span class="bx '+(s.is_active?'ok':'off')+'">'+(s.is_active?'active':'inactive')+'</span></td></tr>'+
    '<tr><td>Capability</td><td>'+(s.capability||'—')+'</td></tr>'+
    '<tr><td>Description</td><td style="white-space:pre-wrap">'+(s.description||'—')+'</td></tr>'+
    '<tr><td>Skills</td><td>'+skills+'</td></tr>'+
    '<tr><td>Examples</td><td>'+examples+'</td></tr>'+
    '<tr><td>Start cmd</td><td><code>'+(s.start_cmd||'—')+'</code></td></tr>'+
    '</table>'+
    // Tool list section (filled async below)
    '<div id="det-tools-sec" style="border-top:1px solid var(--bd);padding:12px 18px">'+
      `<div style="font-size:11px;font-weight:600;color:var(--mu);text-transform:uppercase;margin-bottom:8px">&#128295; Live Tool List <button class="btn btn-s" onclick="detRefreshTools('${id}')" id="det-tool-refresh-btn">&#8635; Load</button></div>`+
      '<div id="det-tools-body" style="color:var(--mu);font-size:12px">Click Load to fetch live tool list from server</div>'+
    '</div>'+
    '<div style="padding:10px 18px 4px;border-top:1px solid var(--bd)">'+
      '<div style="font-size:11px;color:var(--mu);margin-bottom:6px">Replace &lt;MCP_API_KEY&gt; with your actual token from .env. Use Test/Tools buttons to run with real token.</div>'+
      '<div style="font-size:11px;font-weight:600;color:var(--mu);text-transform:uppercase;margin-bottom:6px">Required headers <button class="btn btn-s" onclick="copyDetPre(this.dataset.p,this)" data-p="det-hdr">Copy</button></div>'+
      '<pre id="det-hdr" style="background:var(--bg3);border:1px solid var(--bd);border-radius:6px;padding:10px;font-size:11px;white-space:pre-wrap;color:var(--gr);margin:0 0 10px">'+headersBlock+'</pre>'+
      '<div style="font-size:11px;font-weight:600;color:var(--mu);text-transform:uppercase;margin-bottom:6px">Test/ping curl <button class="btn btn-s" onclick="copyDetPre(this.dataset.p,this)" data-p="det-ct">Copy</button></div>'+
      '<pre id="det-ct" style="background:var(--bg3);border:1px solid var(--bd);border-radius:6px;padding:10px;font-size:11px;white-space:pre-wrap;color:var(--gr);margin:0 0 10px">'+curlTest+'</pre>'+
      '<div style="font-size:11px;font-weight:600;color:var(--mu);text-transform:uppercase;margin-bottom:6px">Tools/list curl <button class="btn btn-s" onclick="copyDetPre(this.dataset.p,this)" data-p="det-tl">Copy</button></div>'+
      '<pre id="det-tl" style="background:var(--bg3);border:1px solid var(--bd);border-radius:6px;padding:10px;font-size:11px;white-space:pre-wrap;color:var(--gr);margin:0">'+curlTools+'</pre>'+
    '</div>';
  document.getElementById('detmo').style.display='flex';
  // Auto-load tools
  detRefreshTools(id);
}
async function detRefreshTools(id){
  const el=document.getElementById('det-tools-body');
  const btn=document.getElementById('det-tool-refresh-btn');
  if(!el)return;
  if(btn)btn.textContent='Loading…';
  try{
    const d=await api('GET','/servers/'+encodeURIComponent(id)+'/tools');
    const tools=d.tools||[];
    if(!d.ok){
      el.innerHTML='<span style="color:var(--rd)">Error: '+(d.error||'probe failed')+'</span>'
        +'<span style="font-size:11px;color:var(--mu)"> · '+d.latency_ms+'ms</span>';
    } else if(!tools.length){
      el.innerHTML='<span style="color:var(--mu)">No tools returned (server online · '+d.latency_ms+'ms)</span>';
    } else {
      el.innerHTML='<div style="font-size:11px;color:var(--mu);margin-bottom:6px">'+tools.length+' tools · '+d.latency_ms+'ms · key: <span style="color:'+(d.key_source==='per-server-db'?'var(--gr)':'var(--yw)')+'">'+d.key_source+'</span></div>'+
        '<table class="tbl" style="font-size:12px"><thead><tr><th>Tool name</th><th>Description</th></tr></thead><tbody>'+
        tools.map(function(t){
          return '<tr><td><code style="font-size:11px">'+t.name+'</code></td>'
            +'<td style="color:var(--mu);font-size:12px">'+(t.description||'—').replace(/</g,'&lt;')+'</td></tr>';
        }).join('')+'</tbody></table>';
    }
    if(btn)btn.textContent='&#8635; Refresh';
  }catch(e){
    if(el)el.innerHTML='<span style="color:var(--rd)">'+e.message+'</span>';
    if(btn)btn.textContent='&#8635; Retry';
  }
}
function closeDet(e){
  if(e&&e.target!==document.getElementById('detmo'))return;
  document.getElementById('detmo').style.display='none';
}

let _credServerId='';
async function showCreds(id){
  _credServerId=id;
  document.getElementById('credmo-title').textContent='&#128273; Credentials — '+id;
  document.getElementById('cred-key').value='';
  document.getElementById('cred-exp').value='';
  document.getElementById('credmo-status').innerHTML='<span style="color:var(--mu)">Loading…</span>';
  document.getElementById('credmo').style.display='flex';
  try{
    const d=await api('GET','/api/mcp-credentials');
    const srv=(d.servers||[]).find(s=>s.id===id)||{};
    const set=srv.api_key_set;
    const hint=srv.api_key_hint||'';
    const exp=srv.api_key_expires;
    const expStr=exp?'  ·  expires '+new Date(exp).toLocaleString():'  ·  no expiry set';
    document.getElementById('credmo-status').innerHTML=set
      ?`<span style="color:var(--gr)">&#10003; Key set</span>  <code style="font-size:12px;color:var(--mu)">${hint}</code>${expStr}`
      :`<span style="color:var(--yw)">&#9888; No per-server key — using <code>MCP_API_KEY</code> env var</span>`;
  }catch(e){
    document.getElementById('credmo-status').innerHTML='<span style="color:var(--rd)">Could not load status: '+e.message+'</span>';
  }
}
function closeCreds(e){
  if(e&&e.target!==document.getElementById('credmo'))return;
  document.getElementById('credmo').style.display='none';
}
function toggleCredVis(){
  const inp=document.getElementById('cred-key');
  inp.type=inp.type==='password'?'text':'password';
}
async function saveCreds(){
  const key=document.getElementById('cred-key').value.trim();
  if(!key){alert('Enter an API key / Bearer token to save.');return;}
  const exp=parseInt(document.getElementById('cred-exp').value)||null;
  const body={api_key:key};
  if(exp)body.expires_hours=exp;
  try{
    await api('PUT','/api/mcp-credentials/'+encodeURIComponent(_credServerId),body);
    document.getElementById('credmo').style.display='none';
    loadSrv();
  }catch(e){alert('Save failed: '+e.message);}
}
async function clearCreds(){
  if(!confirm('Remove per-server key for '+_credServerId+'? The agent will fall back to the MCP_API_KEY env var.'))return;
  try{
    await api('DELETE','/api/mcp-credentials/'+encodeURIComponent(_credServerId));
    document.getElementById('credmo').style.display='none';
    loadSrv();
  }catch(e){alert('Clear failed: '+e.message);}
}
async function genCredJwt(){
  const sub=(document.getElementById('cg-sub').value||'fab-agent').trim();
  const roles=(document.getElementById('cg-roles').value||'agent').split(',').map(s=>s.trim()).filter(Boolean);
  const hours=parseInt(document.getElementById('cg-hours').value)||24;
  try{
    const r=await api('POST','/api/auth/token',{sub,roles,hours});
    // Auto-fill key field and expiry
    document.getElementById('cred-key').value=r.token;
    document.getElementById('cred-key').type='text';
    document.getElementById('cred-exp').value=hours;
    // Show preview
    document.getElementById('cg-out').style.display='block';
    document.getElementById('cg-preview').textContent=r.token;
    document.getElementById('cg-meta').textContent=
      'sub='+sub+'  roles=['+roles.join(',')+']  expires: '+new Date(r.expires_at*1000).toLocaleString();
  }catch(e){alert('JWT generation failed: '+e.message);}
}
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mode = "LLM agent" if HUB_LLM_ENABLED else "first-match (set HUB_LLM_ENABLED=true for LLM)"
    auth_status = (
        f"enabled ({AUTH_PROVIDER} provider)"
        if AUTH_ENABLED else "disabled (AUTH_ENABLED=false)"
    )
    print(f"FAB MCP Hub Server  {HUB_HOST}:{HUB_PORT}")
    print(f"Registry : MySQL fab_semantic.mcp_servers (seed: python scripts/seed_hub_db.py)")
    print(f"Routing  : {mode}")
    print(f"Auth     : {auth_status}  |  RBAC: admin / agent / readonly")
    import pathlib as _pl
    _log_file = _pl.Path(__file__).resolve().parent.parent / "logs" / "hub.log"
    print(f"Logs     : GET /api/logs (in-memory) | GET /api/logs/file | file: {_log_file}")
    uvicorn.run(app, host=HUB_HOST, port=HUB_PORT, log_level="warning")
