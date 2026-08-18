"""
FAB MCP Hub Agent
=================
Hub discovers the right MCP server(s). Agent loads tools live and runs a
native ReAct loop — in parallel when multiple servers are selected.

Full request flow on each call to run_agent():
    1. /auth/login      → hub issues RS256 JWT for the agent
    2. POST /discover   → hub validates JWT, routes query, returns per-server tokens
    3. load_mcp_tools   → fresh tool discovery from each selected MCP server
    4. create_react_agent(llm, tools) → per-server ReAct loop using LangGraph
    5. asyncio.gather   → parallel execution when multiple servers are matched

Per-server token scoping:
    Each server in the /discover response carries a short-lived (1 h) RS256 JWT
    with audience = server_id. The target MCP server's FastMCP JWTVerifier checks
    that the audience matches its own server ID, so a token issued for server A
    cannot be replayed against server B — cross-server token reuse is blocked by
    design without any application-level check.

Authentication Architecture — two independent directions:
    OUTBOUND (agent sends tokens):
      Agent ──credentials──► Hub           hub validates via hub_service/auth.py
      Agent ──hub JWT──────► Hub /discover hub validates via hub_service/auth.py
      Agent ──server JWT───► MCP /mcp      MCP validates via FastMCP JWTVerifier + JWKS

    INBOUND VERIFICATION (agent verifies tokens it receives — defense-in-depth):
      Hub ──access_token──► Agent          verified via _verify_hub_token() + JWKS
      Hub ──server_token──► Agent          verified via _verify_hub_token(aud=id) + JWKS

    Both directions use the same JWKS endpoint: GET /.well-known/jwks.json
    _decode_jwt_claims() is OBSERVABILITY ONLY — never used for access-control.

Configuration (read from .env via python-dotenv):
    OLLAMA_BASE_URL         Ollama endpoint  (default: http://localhost:11434/v1)
    OLLAMA_MODEL            Model name       (default: llama3.2:3b)
    HUB_SERVER_URL          Hub base URL     (default: http://localhost:8090)

    Hub login (preferred — hub issues RS256 JWT):
      HUB_AGENT_USERNAME    username for POST /auth/login
      HUB_AGENT_PASSWORD    password for POST /auth/login

    Static key fallback (backward-compatible; used when login credentials are absent):
      HUB_API_KEY           Bearer token accepted by hub /discover
      MCP_API_KEY           Fallback Bearer token sent to MCP servers directly
"""

import asyncio
import json
import os
import pathlib
import re
from contextlib import asynccontextmanager

# ─── Environment loading ──────────────────────────────────────────────────────
# Load .env before reading any os.environ values.
# This must happen before the module-level constant assignments below, because
# Python evaluates module-level statements top-to-bottom. Placing load_dotenv()
# after the constants would mean the constants see the un-patched environment.
# The agent runs from the project root, so we resolve the path relative to this
# file rather than using a hard-coded path or relying on the working directory.
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(pathlib.Path(__file__).resolve().parent / ".env")
except ImportError:
    pass  # python-dotenv is optional; fall back to whatever the shell environment provides

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client
from langchain_openai import ChatOpenAI
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_core.messages import AIMessage, HumanMessage
import warnings as _warnings
# Suppress LangGraph v1.x deprecation: fires at call site, not import time.
# Migration to langchain.agents not applicable — langchain package not installed.
_warnings.filterwarnings("ignore", message="create_react_agent")
from langgraph.prebuilt import create_react_agent

# ─── Configuration constants ─────────────────────────────────────────────────
OLLAMA_URL     = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
MODEL          = os.environ.get("OLLAMA_MODEL",    "llama3.2:3b")
HUB_SERVER_URL = os.environ.get("HUB_SERVER_URL",  "http://localhost:8090")

# ── MCP context enrichment toggle ─────────────────────────────────────────────
# When True (default), the agent discovers MCP prompt templates and resource
# documents and injects them into the ReAct context before running tool loops.
# When False, only MCP tools are used — simpler, 2-5 fewer round-trips, and
# easier to debug (no prompt matching, no resource reads).
#
# Override at runtime:
#   env var:  AGENT_CONTEXT_ENABLED=false
#   CLI flag: python agent.py --no-context "<query>"
#   API call: run_agent(query, use_context=False)
_DEFAULT_CONTEXT: bool = os.environ.get("AGENT_CONTEXT_ENABLED", "true").lower() \
    not in ("0", "false", "no", "off")

# Hub agent login credentials (preferred authentication path).
# When both are set, the agent exchanges them for an RS256 JWT via POST /auth/login.
# The JWT is then used for /discover and carries sub + roles that the hub logs.
_HUB_AGENT_USERNAME = os.environ.get("HUB_AGENT_USERNAME", "")
_HUB_AGENT_PASSWORD = os.environ.get("HUB_AGENT_PASSWORD", "")

# Static-key fallbacks kept for backward compatibility.
# HUB_API_KEY  → sent to the hub when login credentials are not set.
# MCP_API_KEY  → sent directly to MCP servers when no per-server JWT is available.
HUB_API_KEY = os.environ.get("HUB_API_KEY", "")
MCP_API_KEY = os.environ.get("MCP_API_KEY", "")

# ─── LLM client ──────────────────────────────────────────────────────────────
# ChatOpenAI is used in OpenAI-compatible mode, pointing at the local Ollama
# server. The "ollama" API key is a dummy value — Ollama doesn't validate it,
# but the OpenAI client library requires a non-empty string.
#
# temperature=0 ensures deterministic tool selection and routing. With any
# non-zero temperature the model might randomly skip a tool call or hallucinate
# an answer instead of calling the correct tool.
#
# request_timeout=240 is generous because llama3.2:3b on CPU can take 30–60 s
# for a complex multi-tool ReAct turn.
llm = ChatOpenAI(
    base_url=OLLAMA_URL,
    openai_api_key="ollama",    # placeholder — Ollama ignores the API key value
    model=MODEL,
    temperature=0,              # deterministic — prevents random tool-call omissions
    request_timeout=240,        # seconds; llama3.2 on CPU can be slow
)


# =============================================================================
# AUTHENTICATION LAYER
# =============================================================================
#
# This agent participates in authentication in TWO distinct directions.
# Read this block first — it is the map for all auth code below.
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │ DIRECTION 1 — OUTBOUND  (agent presents tokens to external services)    │
# │                                                                         │
# │  Step 1a  Agent ──username+password──► POST /auth/login                │
# │           VALIDATOR: hub_service/auth.py  (PBKDF2 credential check)    │
# │           RESULT:    Hub returns RS256 JWT  (exp=8h, iss=fab-mcp-hub)  │
# │                                                                         │
# │  Step 1b  Agent ──Bearer <hub JWT>──► POST /discover                   │
# │           VALIDATOR: hub_service/auth.py  verify_token()               │
# │           (RS256 sig + iss + exp + role=agent check)                   │
# │                                                                         │
# │  Step 1c  Agent ──Bearer <server JWT>──► MCP Server  POST /mcp         │
# │           VALIDATOR: FastMCP JWTVerifier  (RS256 via JWKS)             │
# │           (sig + iss + aud=server_id + exp — every JSON-RPC call)      │
# │           ENFORCER:  require_role() inside every MCP tool function      │
# └─────────────────────────────────────────────────────────────────────────┘
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │ DIRECTION 2 — INBOUND VERIFICATION  (agent verifies tokens it receives) │
# │ Defense-in-depth: catches forged/tampered tokens before use.            │
# │ Uses the same JWKS endpoint MCP servers use.                            │
# │                                                                         │
# │  Step 2a  Hub ──access_token──► _get_hub_token()                       │
# │           VERIFIER: _verify_hub_token()  via GET /.well-known/jwks.json │
# │           Checks: RS256 sig · iss=fab-mcp-hub · exp                    │
# │           Hard fail (raise) → token not cached; login rejected          │
# │                                                                         │
# │  Step 2b  Hub ──server_token[]──► run_agent()  after POST /discover    │
# │           VERIFIER: _verify_hub_token(audience=server_id)  via JWKS    │
# │           Checks: RS256 sig · iss · aud=server_id · exp                │
# │           Hard fail (raise) → that server is skipped entirely          │
# └─────────────────────────────────────────────────────────────────────────┘
#
# OBSERVABILITY (NOT auth):
#   _decode_jwt_claims()  — unverified JWT decode for logging and chat UI only.
#   Never used for access-control decisions.
# =============================================================================

_hub_token_cache: str = ""   # RS256 hub JWT; cached for the process lifetime


async def _get_hub_token() -> str:
    """Acquire and cache the agent's hub JWT.

    DIRECTION 1a + 2a — OUTBOUND login then INBOUND verification.

    Outbound (Step 1a):
        POST /auth/login with agent credentials → hub validates and returns
        an RS256 JWT signed with the hub's RSA private key.

    Inbound verification (Step 2a):
        _verify_hub_token() checks the returned JWT's RS256 signature against
        the hub's public key via GET /.well-known/jwks.json before caching.
        Hard failure (signature/issuer mismatch) → raise; don't cache a bad token.
        Soft failure (JWKS unreachable) → warning logged, token cached anyway
        (TLS provides transport integrity; hard-failing blocks agents on hub restart).

    Token acquisition order:
        1. Return the in-process cache if already populated.
        2. POST /auth/login with HUB_AGENT_USERNAME + HUB_AGENT_PASSWORD.
        3. Fall back to HUB_API_KEY (static pre-shared key) if login fails or
           credentials are not set.
    """
    global _hub_token_cache
    if _hub_token_cache:
        return _hub_token_cache   # already logged in — reuse cached JWT

    if _HUB_AGENT_USERNAME and _HUB_AGENT_PASSWORD:
        try:
            # ── Step 1a: OUTBOUND — send credentials, receive RS256 JWT ─────
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{HUB_SERVER_URL}/auth/login",
                    json={"username": _HUB_AGENT_USERNAME, "password": _HUB_AGENT_PASSWORD},
                    timeout=10.0,
                )
                resp.raise_for_status()
                token = resp.json()["access_token"]

            # ── Step 2a: INBOUND VERIFICATION — verify hub JWT via JWKS ─────
            # The hub signed this JWT with its RSA private key. Verify it against
            # the public key from /.well-known/jwks.json before caching.
            # Hard fail: signature / issuer mismatch → raise (don't cache)
            # Soft fail: JWKS unreachable → warning + proceed (see docstring)
            try:
                claims = await _verify_hub_token(token)
                print(
                    f"[hub]  login     : sub={_HUB_AGENT_USERNAME} "
                    f"(RS256 JWT · JWKS sig ✓ · iss={claims.get('iss')} "
                    f"exp={claims.get('exp')})"
                )
            except Exception as verify_exc:
                # Explicit crypto mismatch — the hub or the token cannot be trusted.
                print(f"[auth] hub JWT FAILED JWKS verification: {verify_exc} — login rejected")
                raise RuntimeError(
                    f"Hub login JWT failed RS256 verification: {verify_exc}"
                ) from verify_exc

            _hub_token_cache = token
            return _hub_token_cache

        except Exception as exc:
            print(f"[hub]  login failed ({exc}), falling back to HUB_API_KEY")

    # Fallback: use the static HUB_API_KEY (or empty string for open-dev mode).
    _hub_token_cache = HUB_API_KEY
    return _hub_token_cache


# =============================================================================
# DIRECTION 1c — OUTBOUND AUTH: Agent → MCP Server
# =============================================================================
# The agent attaches a per-server RS256 JWT to every JSON-RPC call it sends
# to an MCP server. Validation happens entirely on the MCP server side via
# FastMCP JWTVerifier (see datalayer-as-service/mcp_server/server.py).
# This section manages the MCP session and the token that rides with it.
#
# HOW AUTHENTICATION WORKS: AGENT → MCP SERVER
# ─────────────────────────────────────────────
# This system follows the standard JWT auth pattern for MCP exactly:
#
#   Generic pattern                     This implementation
#   ──────────────────────────────────  ──────────────────────────────────────────────────────
#   "Your auth service (FastAPI)"    →  hub_service/hub_server.py  (port 8090)
#   "Issues JWTs"                    →  hub_service/auth.py → generate_server_token()
#   "Publishes public key"           →  GET http://localhost:8090/.well-known/jwks.json
#   "Your MCP server (FastMCP)"      →  datalayer-as-service/mcp_server/customer_server.py
#   "Validates JWTs via JWTVerifier" →  FastMCP(auth=build_jwt_verifier())
#   "Your client"                    →  this file — agent.py
#   "Logs in once"                   →  _get_hub_token() → POST /auth/login → RS256 JWT
#   "Gets per-server tokens"         →  run_agent() → POST /discover → per-server RS256 JWTs
#   "Attaches token to every call"   →  mcp_session() → headers={"Authorization":"Bearer …"}
#
#
# THE TOKEN JOURNEY (step by step)
# ─────────────────────────────────
#
#  Step 1 — Agent logs in to the hub (once per process)
#  ┌─────────────────────────────────────────────────────┐
#  │  POST http://localhost:8090/auth/login               │
#  │  Body: {"username": "agent", "password": "…"}        │
#  │  ↓                                                   │
#  │  Hub verifies credentials → signs RS256 JWT with    │
#  │  hub_service/.keys/private.pem (RSA-2048)            │
#  │  ↓                                                   │
#  │  Response: {"access_token": "eyJhbGci…"}             │
#  └─────────────────────────────────────────────────────┘
#  JWT payload: {"sub":"agent","roles":["agent"],"iss":"fab-mcp-hub","exp":…}
#
#  Step 2 — Agent calls /discover to route the query and get per-server JWTs
#  ┌─────────────────────────────────────────────────────┐
#  │  POST http://localhost:8090/discover                 │
#  │  Authorization: Bearer <hub-jwt>                    │
#  │  Body: {"intent": "customer details for CUST001"}   │
#  │  ↓                                                   │
#  │  Hub validates hub-jwt, runs LLM routing             │
#  │  Hub calls generate_server_token("fab-customer-server") │
#  │  → mints NEW RS256 JWT: aud="fab-customer-server"   │
#  │  ↓                                                   │
#  │  Response: [{                                        │
#  │    "id": "fab-customer-server",                      │
#  │    "endpoint": "http://127.0.0.1:9100/mcp",          │
#  │    "transport": "streamable-http",                   │
#  │    "server_token": "eyJhbGci… aud=fab-customer-server" │
#  │  }]                                                  │
#  └─────────────────────────────────────────────────────┘
#  The per-server JWT is different from the hub JWT: it has aud=server_id so
#  the target MCP server can enforce it was issued for itself specifically.
#
#  Step 3 — mcp_session() opens a transport connection with the per-server JWT
#  ┌─────────────────────────────────────────────────────┐
#  │  Client sends to MCP server on EVERY JSON-RPC call: │
#  │    Authorization: Bearer eyJhbGci… (per-server JWT) │
#  │                                                      │
#  │  FastMCP JWTVerifier runs on every HTTP request:    │
#  │    1. Fetches hub JWKS  → GET /well-known/jwks.json  │
#  │    2. Verifies RS256 signature against public key    │
#  │    3. Checks: iss == "fab-mcp-hub"                   │
#  │    4. Checks: aud == "fab-customer-server"           │
#  │    5. Checks: exp is not in the past                 │
#  │    → Rejects with 401 if ANY check fails            │
#  │    → Passes if all checks pass                       │
#  └─────────────────────────────────────────────────────┘
#
#  Step 4 — BearerClaimsMiddleware extracts claims for per-tool RBAC
#  ┌─────────────────────────────────────────────────────┐
#  │  BearerClaimsMiddleware decodes the validated token  │
#  │  and stores claims in a ContextVar:                 │
#  │    {"sub":"agent","roles":["agent"],"aud":"fab-customer-server"} │
#  │                                                      │
#  │  Inside every tool function:                         │
#  │    require_role("admin","agent")   ← reads ContextVar │
#  │    audit_log("customer_360", …)    ← logs identity   │
#  │    query_customer_360(…)           ← MySQL call       │
#  │    (MySQL uses MYSQL_USER/PASSWORD, not the JWT)     │
#  └─────────────────────────────────────────────────────┘
#
#  KEY SECURITY PROPERTY: audience scoping
#  A token issued for fab-customer-server (aud="fab-customer-server") is
#  REJECTED by fab-pricing-server even if both trust the same hub JWKS.
#  The audience claim acts as a server-specific lock — stolen tokens cannot
#  be replayed against a different server.
#
# ─────────────────────────────────────────────────────────────────────────────

def _auth_headers(token: str) -> dict[str, str]:
    """Build the Authorization header dict from a Bearer token.

    Returns an empty dict when the token is absent so callers can always
    spread the result into a headers dict without an extra None check.
    The empty-dict case occurs in open-dev mode when no keys are configured.
    """
    return {"Authorization": f"Bearer {token}"} if token else {}


@asynccontextmanager
async def mcp_session(server: dict):
    """Open and initialize an authenticated MCP client session for one server.

    This is the "client attaches token to every MCP call" step in the JWT auth
    architecture. The token attached here is the per-server RS256 JWT received
    from hub /discover (server["server_token"]), NOT the hub login JWT.

    Why a separate per-server token?
        The /discover endpoint mints a fresh short-lived JWT for each matched
        server with audience = server_id. This means:
          • The token is cryptographically bound to one specific MCP server.
          • If intercepted, it cannot be replayed against any other server.
          • Each server can independently verify its own token without asking
            the hub (offline validation via JWKS public key cache).

    Transport support:
        SSE (legacy)               — Two channels:
                                     GET /sse  → persistent server→client event stream.
                                     POST /messages → client→server JSON-RPC.
                                     Both channels carry the Authorization header.
                                     The GET stream must stay open for the session
                                     lifetime; closing it invalidates the session_id
                                     and causes subsequent POSTs to return 404.

        Streamable-HTTP (default)  — Single POST endpoint (e.g. /mcp).
                                     Session continuity is tracked via the
                                     Mcp-Session-Id response header. The MCP SDK
                                     re-sends this header automatically; the app
                                     does not need to manage it.

    Token priority (highest wins):
        1. server["server_token"]  — per-server RS256 JWT from hub /discover.
                                     Has aud=server_id; expires in 1 hour.
                                     ← This is the normal production path.
        2. server["api_key"]       — opaque static key from MySQL mcp_servers table
                                     (set via Admin UI → Key button). Used by the
                                     legacy BearerAuthMiddleware on older servers.
        3. MCP_API_KEY env var     — shared static fallback; used when neither of
                                     the above is set (e.g. dev environments where
                                     all servers share one key).

    The token is sent on EVERY JSON-RPC call, not just during handshake:
        POST /mcp  {"method": "initialize", …}   Authorization: Bearer <token>
        POST /mcp  {"method": "tools/list",  …}   Authorization: Bearer <token>
        POST /mcp  {"method": "tools/call",  …}   Authorization: Bearer <token>

    FastMCP's JWTVerifier validates the token on every request by fetching the
    hub's JWKS endpoint and verifying the RS256 signature + iss + aud + exp.
    """
    transport = server.get("transport", "sse")
    endpoint  = server["endpoint"]

    # Resolve the best available token for this server.
    # server["server_token"] is the per-server JWT minted by hub /discover
    # with aud=server_id. This is what FastMCP's JWTVerifier expects to see.
    token   = server.get("server_token") or server.get("api_key") or MCP_API_KEY
    headers = _auth_headers(token)
    # headers is now {"Authorization": "Bearer eyJhbGci…"} (or {} in dev mode).
    # It is passed into the transport client so that every HTTP request to the
    # MCP server — from the initial handshake to every tool call — carries this
    # header. FastMCP's JWTVerifier intercepts each request before it reaches
    # any tool function and validates the token against the hub's JWKS endpoint.

    if transport == "sse":
        # SSE transport: sse_client() opens GET /sse and returns:
        #   r — asyncio stream reader: receives server→client SSE events.
        #   w — asyncio stream writer: sends client→server POST /messages calls.
        # Both carry the Authorization header via the underlying httpx session.
        # Closing the GET stream before the session ends invalidates the session_id;
        # subsequent POSTs would get 404 "session not found".
        async with sse_client(endpoint, headers=headers) as (r, w):
            async with ClientSession(r, w) as session:
                # initialize() is the MCP protocol handshake: sends
                # {"method":"initialize","params":{"protocolVersion":"…","capabilities":{}}}
                # and receives the server's supported version + capabilities.
                # The token is validated by JWTVerifier on this first request.
                # If validation fails, raise_for_status() raises here and the
                # except block in _run_on_server classifies it as an auth error.
                await session.initialize()
                yield session
    else:
        # Streamable-HTTP transport: single POST endpoint.
        # streamablehttp_client() returns:
        #   r — read stream for responses.
        #   w — write stream for sending requests.
        #   _ — session-ID accessor callback (unused; SDK handles it internally).
        async with streamablehttp_client(endpoint, headers=headers) as (r, w, _):
            async with ClientSession(r, w) as session:
                await session.initialize()
                yield session


# ─── Utility helpers ─────────────────────────────────────────────────────────

def _fmt(obj, limit: int = 200) -> str:
    """Compact single-line representation of any object, truncated to limit chars.

    Used for terminal log lines only — keeps logs scannable without truncating
    the actual data returned to callers.
    """
    try:
        s = json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        s = str(obj)
    return s[:limit] + ("…" if len(s) > limit else "")


def _token_hint(token: str) -> str:
    """Return the first 10 characters of a token for display purposes.

    Enough to distinguish tokens visually without exposing the full secret.
    Falls back to "dev-open" label when no token is present (open-dev mode).
    """
    return (token[:10] + "…") if len(token) > 10 else (token or "dev-open")


# ── [OBSERVABILITY ONLY — NOT AUTHENTICATION] ─────────────────────────────
def _decode_jwt_claims(token: str) -> dict:
    """Decode JWT payload claims WITHOUT verifying the signature.

    *** OBSERVABILITY ONLY — never used for access-control decisions. ***

    Purpose: extract sub / roles / aud / exp / iss from a token so they can be
    logged and displayed in the chat UI Security tab. The signature is NOT
    checked here — use _verify_hub_token() for that.

    Returns an empty dict for non-JWT tokens (static API keys, empty strings).
    """
    if not token:
        return {}
    try:
        import jwt  # PyJWT — imported lazily to keep startup fast when absent
        return jwt.decode(token, options={"verify_signature": False})
    except Exception:
        # Token is not a JWT (e.g., a static hex API key) — that is expected;
        # return empty dict so callers can use .get() safely.
        return {}


# =============================================================================
# DIRECTION 2 — INBOUND VERIFICATION: Agent verifies tokens from Hub
# =============================================================================
# The hub issues RS256 JWTs to the agent (login response) and per-server
# tokens (discover response). Before using any of these, the agent verifies
# the RS256 signature against the hub's public key from /.well-known/jwks.json.
#
# This mirrors exactly what MCP servers do for the tokens the AGENT sends them:
#   MCP servers  →  verify agent→MCP tokens via GET /.well-known/jwks.json
#   Agent (here) →  verify hub→agent tokens via GET /.well-known/jwks.json
#
# Both sides verify via the SAME JWKS endpoint. Same key, same algorithm.
#
# Failure tiers — by design:
#   HARD (always raise):
#     - Invalid RS256 signature   → token was forged or tampered
#     - Wrong issuer (iss)        → token was not minted by this hub
#     - Wrong audience (aud)      → server token used on wrong server
#     - Token expired (exp)       → caller must re-authenticate
#
#   SOFT (log WARNING + proceed with unverified decode):
#     - JWKS endpoint unreachable → hub may be starting up; TLS covers transit
#       (hard-failing here would block agents on every hub restart — bad trade-off)
#
# Key rotation: increment HUB_JWT_KID on the hub → restart hub. The PyJWKClient
# cache refreshes automatically on the next call with the new kid.
# =============================================================================

_jwks_client = None   # module-level PyJWKClient, lazily initialised


def _jwks():
    """Return the module-level cached PyJWKClient for the hub JWKS endpoint.

    Created once on first call; keys are cached locally for 5 minutes before
    re-fetching. This avoids an HTTP round-trip on every token verification.

    JWKS endpoint called:
        GET http://localhost:8090/.well-known/jwks.json

    Example JWKS response:
        {
          "keys": [{
            "kty": "RSA",
            "kid": "hub-rsa-1",      ← matched against JWT header "kid"
            "use": "sig",
            "alg": "RS256",
            "n":   "0vx7agoebGcQSuuPiLJXZptN9nndrQmbXEps2...",   ← RSA modulus
            "e":   "AQAB"                                          ← RSA exponent
          }]
        }

    Returns None when PyJWT ≥2.4 is not installed; callers fall back to an
    unverified decode (logged as WARNING).
    """
    global _jwks_client
    if _jwks_client is None:
        try:
            from jwt import PyJWKClient as _PyJWKClient  # PyJWT ≥2.4
            _jwks_client = _PyJWKClient(
                f"{HUB_SERVER_URL}/.well-known/jwks.json",
                cache_keys=True,
                lifespan=300,   # re-fetch JWKS every 5 minutes
            )
        except Exception:
            pass   # PyJWT not installed or wrong version
    return _jwks_client


async def _verify_hub_token(token: str, *, audience: str | None = None) -> dict:
    """[DIRECTION 2 — INBOUND VERIFICATION] Verify an RS256 JWT from the hub via JWKS.

    Implements the standard 6-step JWKS verification flow:

        Step 1 — Read JWT header, extract kid and alg
                 Example header:  {"alg": "RS256", "typ": "JWT", "kid": "hub-rsa-1"}

        Step 2 — Fetch JWKS from the hub's public endpoint (or return cached keys)
                 GET http://localhost:8090/.well-known/jwks.json

        Step 3 — Find the JWK whose kid matches the JWT header kid
                 kid="hub-rsa-1"  →  selects the matching key object in the JWKS array

        Step 4 — Convert the JWK (n, e, kty=RSA) into an RSA public key object
                 cryptography.hazmat RSAPublicKey built from modulus + exponent

        Steps 1-4 are performed by:
            signing_key = PyJWKClient.get_signing_key_from_jwt(token)

        Step 5 — Verify RS256 signature with the RSA public key
                 jwt.decode(..., signing_key.key, algorithms=["RS256"])

        Step 6 — Validate claims
                 iss  — must equal "fab-mcp-hub"       (env: HUB_JWT_ISSUER)
                 aud  — must equal audience arg         (per-server tokens only)
                 exp  — must be in the future           (always enforced)
                 nbf  — must be in the past             (enforced by PyJWT when present)

        Both steps 5-6 are performed by jwt.decode().

    Called by:
        _get_hub_token()  → verifies the hub login JWT   (Step 2a, no audience)
        run_agent()       → verifies each server token   (Step 2b, audience=server_id)

    Example — hub login JWT payload (Step 2a, audience=None):
        {
          "sub":   "agent",
          "roles": ["agent"],
          "iss":   "fab-mcp-hub",
          "iat":   1785857461,
          "nbf":   1785857461,
          "exp":   1785943861        ← 8 hours from iat
        }

    Example — per-server JWT payload (Step 2b, audience="fab-customer-server"):
        {
          "sub":   "agent",
          "roles": ["agent"],
          "iss":   "fab-mcp-hub",
          "aud":   "fab-customer-server",   ← must match audience arg exactly
          "iat":   1785857461,
          "nbf":   1785857461,
          "exp":   1785861061               ← 1 hour from iat
        }

    Returns:
        Verified claims dict identical to the payload examples above.

    Hard failures (always re-raise — never use this token):
        jwt.exceptions.ExpiredSignatureError    exp is in the past; re-authenticate
        jwt.exceptions.InvalidSignatureError    RSA signature mismatch; possible forgery
        jwt.exceptions.InvalidAudienceError     aud ≠ audience arg; wrong server token
        jwt.exceptions.InvalidIssuerError       iss ≠ "fab-mcp-hub"; wrong issuer

    Soft failure (log WARNING + return unverified claims):
        Any other exception (network error, hub not yet started, PyJWKClientError)
        → JWKS temporarily unavailable; TLS covers transit integrity in the meantime
    """
    import jwt as _jwt  # PyJWT

    client = _jwks()
    if client is None:
        # PyJWT / PyJWKClient not installed — unverified decode for observability
        print("[auth] WARNING: PyJWKClient unavailable — RS256 signature not verified")
        return _jwt.decode(token, options={"verify_signature": False})

    try:
        # Steps 1-4: read JWT header → fetch JWKS → match kid → build RSA public key.
        # get_signing_key_from_jwt() is synchronous (may do an HTTP fetch on cache miss).
        # Run in a thread executor to avoid blocking the asyncio event loop.
        #
        # Internally PyJWKClient does:
        #   jwt_header = jwt.get_unverified_header(token)
        #   kid = jwt_header["kid"]                    # e.g. "hub-rsa-1"
        #   alg = jwt_header["alg"]                    # "RS256"
        #   jwks  = GET /.well-known/jwks.json  (or cache)
        #   jwk   = next(k for k in jwks["keys"] if k["kid"] == kid)
        #   key   = RSAPublicKey(n=jwk["n"], e=jwk["e"])
        signing_key = await asyncio.to_thread(client.get_signing_key_from_jwt, token)

        # Step 5 + 6: verify RS256 signature, then validate claims.
        # PyJWT checks: signature (step 5), iss, exp, nbf (if present), aud (step 6).
        # nbf (not-before) is validated automatically when the claim exists in the token.
        issuer = os.environ.get("HUB_JWT_ISSUER", "fab-mcp-hub")
        decode_kwargs: dict = {"algorithms": ["RS256"], "issuer": issuer}
        if audience:
            decode_kwargs["audience"] = audience          # Step 2b: aud=server_id enforced
        else:
            decode_kwargs["options"] = {"verify_aud": False}   # Step 2a: hub login, no aud

        payload = _jwt.decode(token, signing_key.key, **decode_kwargs)
        return payload

    # ── Hard failures — explicit security violations ──────────────────────────
    except _jwt.exceptions.ExpiredSignatureError:
        raise   # exp is in the past; caller must re-authenticate
    except (
        _jwt.exceptions.InvalidSignatureError,
        _jwt.exceptions.InvalidAudienceError,
        _jwt.exceptions.InvalidIssuerError,
    ):
        raise   # cryptographic mismatch — never use this token

    # ── Soft failure — connectivity / parsing issue ───────────────────────────
    except Exception as exc:
        print(
            f"[auth] WARNING: JWKS fetch failed ({type(exc).__name__}: {exc}) "
            "— proceeding without RS256 signature verification"
        )
        return _jwt.decode(token, options={"verify_signature": False})


# ─── Entity ID extraction ─────────────────────────────────────────────────────
# Used by _fetch_mcp_context() to map entity IDs mentioned in the user's
# natural-language query to the structured args that MCP prompts expect.
# These patterns are intentionally simple — the goal is a best-effort extraction,
# not a parser. If no ID is found, prompt args default to empty strings.

_CUST_ID_RE = re.compile(r"\b(CUST\d+)\b", re.IGNORECASE)
_DEAL_ID_RE = re.compile(r"\b(DEAL\d+)\b", re.IGNORECASE)


# ─── MCP Prompts + Resources — context enrichment ───────────────────────────

async def _fetch_mcp_context(
    session: "ClientSession",
    query: str,
    server_id: str,
    on_event=None,
) -> tuple[list[tuple[str, str]], str]:
    """Discover and apply MCP server prompts and resources to enrich the query.

    MCP servers can expose two capability types beyond tools:
      Prompts   — reusable templated message workflows (session.list_prompts /
                  session.get_prompt). For the pricing server these are structured
                  5-step analysis frameworks tailored to specific use cases.
      Resources — reference documents identified by URI (session.list_resources /
                  session.read_resource). For the pricing server these are the
                  policy rules, competitor action guide, and live segment benchmarks.

    Flow:
      1. list_prompts()  → catalogue what prompt templates exist.
      2. Match the user query to the most relevant prompt by keyword.
      3. Extract entity IDs (CUST / DEAL) from the query to fill prompt args.
      4. get_prompt(name, args) → get the structured message list.
      5. list_resources() → catalogue what reference docs exist.
      6. read_resource(uri) → fetch static reference docs (policy, guide).
         Dynamic resources (live DB queries) are read only when referenced by URI
         in the matched prompt name; all others are skipped to avoid latency.
      7. Emit a mcp_capabilities event so the chat UI can display what was used.

    Returns:
        prompt_messages  — list of (role, text) tuples from the matched prompt,
                           or [] when no prompt matched or the server has none.
        resource_context — concatenated text of auto-read reference resources,
                           or "" when no resources exist or reads all failed.

    Errors are always caught and logged — a failure in this function must never
    prevent the main ReAct tool loop from running.
    """
    prompt_messages:  list[tuple[str, str]] = []
    resource_context: str                   = ""
    prompts_meta:     list[dict]            = []
    resources_meta:   list[dict]            = []

    # ── 1. Discover prompts ───────────────────────────────────────────────────
    try:
        pr = await session.list_prompts()
        prompts_meta = [
            {
                "name":        p.name,
                "description": p.description or "",
                "arguments":   [
                    {"name": a.name, "required": a.required}
                    for a in (p.arguments or [])
                ],
            }
            for p in (pr.prompts or [])
        ]
        if prompts_meta:
            print(f"[mcp]  prompts   : {', '.join(p['name'] for p in prompts_meta)}")
    except Exception as exc:
        print(f"[mcp]  prompts not available ({type(exc).__name__}: {exc})")

    # ── 2. Discover resources ─────────────────────────────────────────────────
    try:
        rr = await session.list_resources()
        resources_meta = [
            {
                "uri":         str(r.uri),
                "name":        r.name,
                "description": r.description or "",
                "mimeType":    r.mimeType or "text/plain",
            }
            for r in (rr.resources or [])
        ]
        if resources_meta:
            print(f"[mcp]  resources : {', '.join(r['uri'] for r in resources_meta)}")
    except Exception as exc:
        print(f"[mcp]  resources not available ({type(exc).__name__}: {exc})")

    # ── 3. Emit capabilities event (chat UI + hub observability via bridge) ─────
    if on_event and (prompts_meta or resources_meta):
        print(
            f"[mcp]  cap event : {len(prompts_meta)} prompt(s), "
            f"{len(resources_meta)} resource(s) → mcp_capabilities"
        )
        try:
            await on_event({
                "type":      "mcp_capabilities",
                "server_id": server_id,
                "prompts":   prompts_meta,
                "resources": resources_meta,
            })
        except Exception as _cap_exc:
            print(f"[mcp]  on_event(mcp_capabilities) failed: {_cap_exc}")

    # ── 4. Match the user query to a prompt + extract entity IDs ─────────────
    if prompts_meta:
        cust_m = _CUST_ID_RE.search(query)
        deal_m = _DEAL_ID_RE.search(query)
        customer_id = cust_m.group(1).upper() if cust_m else ""
        deal_id     = deal_m.group(1).upper() if deal_m else ""

        q_lower      = query.lower()
        prompt_name: str | None = None
        prompt_args: dict       = {}

        # Keyword routing — most specific patterns first
        if any(kw in q_lower for kw in
               ("exception", "non-compliant", "non_compliant", "breach", "violat", "complian")):
            candidate = next(
                (p for p in prompts_meta if "exception" in p["name"] or "policy" in p["name"]),
                None,
            )
            if candidate:
                prompt_name = candidate["name"]
                prompt_args = {"customer_id": customer_id} if customer_id else {}

        elif any(kw in q_lower for kw in
                 ("competitor", "compare", "match", "counter", "escalate", "reject", "competitive")):
            candidate = next(
                (p for p in prompts_meta if "competitor" in p["name"] or "strategy" in p["name"]),
                None,
            )
            if candidate:
                prompt_name = candidate["name"]
                prompt_args = {
                    **({"customer_id": customer_id} if customer_id else {}),
                    **({"deal_id": deal_id} if deal_id else {}),
                }

        elif any(kw in q_lower for kw in
                 ("pricing", "price", "recommend", "analy", "deal", "margin", "trace")):
            # Default: comprehensive deal pricing analysis
            candidate = next(
                (p for p in prompts_meta if "analyz" in p["name"] or "pricing" in p["name"]),
                None,
            )
            if candidate:
                prompt_name = candidate["name"]
                prompt_args = {
                    **({"customer_id": customer_id} if customer_id else {}),
                    **({"deal_id": deal_id} if deal_id else {}),
                }

    # ── 5. Fetch the matched prompt's messages ────────────────────────────────
    if prompt_name:
        try:
            prompt_result = await session.get_prompt(prompt_name, prompt_args or None)
            for msg in (prompt_result.messages or []):
                content_obj = msg.content
                text = content_obj.text if hasattr(content_obj, "text") else str(content_obj)
                prompt_messages.append((str(msg.role), text))
            print(
                f"[mcp]  prompt    : '{prompt_name}' args={prompt_args} "
                f"→ {len(prompt_messages)} message(s)"
            )
            if on_event:
                await on_event({
                    "type":          "mcp_prompt_used",
                    "server_id":     server_id,
                    "prompt_name":   prompt_name,
                    "prompt_args":   prompt_args,
                    "message_count": len(prompt_messages),
                })
        except Exception as exc:
            print(f"[mcp]  prompt '{prompt_name}' fetch failed: {exc}")
            prompt_messages = []

    # ── 6. Read auto-fetchable reference resources ────────────────────────────
    # Only fetch static reference documents (policy rules, guides) automatically.
    # Dynamic/large resources (e.g. live DB dumps) are skipped unless the prompt
    # specifically calls for them — reads are filtered by URI keyword.
    auto_read_keywords = ("policy", "guide", "rule", "action")
    resource_parts: list[str] = []
    for res in resources_meta:
        uri = res["uri"].lower()
        if any(kw in uri for kw in auto_read_keywords):
            try:
                content_result = await session.read_resource(res["uri"])
                for item in (content_result.contents or []):
                    text = item.text if hasattr(item, "text") else str(item)
                    if text.strip():
                        resource_parts.append(f"### {res['name']}\n{text}")
                        print(f"[mcp]  resource  : '{res['uri']}' ({len(text)} chars)")
            except Exception as exc:
                print(f"[mcp]  resource '{res['uri']}' read failed: {exc}")

    resource_context = "\n\n".join(resource_parts)
    return prompt_messages, resource_context


# ─── Per-server ReAct execution ──────────────────────────────────────────────

async def _run_on_server(
    server: dict,
    query: str,
    on_event=None,
    use_context: bool = True,
) -> str:
    """Connect to one MCP server, discover its tools, run a ReAct loop, return the answer.

    This function is the core of the agent's tool-use loop:
      1. Open an authenticated MCP session (streamable-HTTP).
      2. Call load_mcp_tools() — discovers all tools the server exposes and wraps
         them as LangChain-compatible Tool objects.
      3. [if use_context] Call _fetch_mcp_context() — lists prompts and resources,
         matches a prompt template to the query, reads reference resource documents,
         and emits mcp_capabilities / mcp_prompt_used trace events.
      4. create_react_agent() builds a LangGraph ReAct graph:
         - LLM decides which tool to call and with what arguments.
         - Tool is executed via the MCP session.
         - LLM receives the tool result and decides to call another tool or answer.
         - Loop continues until the LLM emits a final text response.
      5. astream_events(version="v2") streams fine-grained events:
         - on_tool_start  → agent is invoking a tool
         - on_tool_end    → tool returned a result
         - on_chat_model_end → LLM produced output (tool decision or final answer)

    Args:
        server:      Server config dict from hub /discover response.
        query:       Natural-language user query.
        on_event:    Optional async callback for streaming trace events to the
                     chat UI. Events are fire-and-forget from this function's
                     perspective; the caller (chat_server.py) queues them.
        use_context: When True (default), discover and inject MCP prompt templates
                     and resource documents before running the ReAct loop.
                     When False, use only MCP tools — simpler, faster, fewer
                     MCP round-trips. Set via AGENT_CONTEXT_ENABLED=false or
                     run_agent(use_context=False).
    """
    server_id = server.get("id", "?")
    token     = server.get("server_token") or server.get("api_key") or MCP_API_KEY
    hint      = _token_hint(token)
    claims    = _decode_jwt_claims(token)   # unverified decode for observability

    # Determine the token source for logging and the Auth tab in the chat UI.
    # This helps operators diagnose key mismatches without reading raw tokens.
    key_src = "server-token" if server.get("server_token") else (
              "per-server-db" if server.get("api_key") else (
              "env-MCP_API_KEY" if MCP_API_KEY else "none"))

    print(f"[mcp]  connecting : {server_id} ({server.get('transport', 'sse')}) {server['endpoint']}")
    print(f"[auth] server={server_id} key_source={key_src} aud={claims.get('aud', '?')}")

    # ── Emit pre-connection trace events ─────────────────────────────────────
    # mcp_connecting is emitted before the TCP connection is attempted so the
    # chat UI can show "connecting…" state immediately rather than waiting for
    # the full MCP handshake (which may take 1–3 s on slow servers).
    #
    # auth_hop records the exact token and claims used at the agent → MCP
    # boundary. This appears in the chat UI's Security tab so operators can
    # verify that the correct JWT audience was used.
    if on_event:
        await on_event({
            "type":      "mcp_connecting",
            "server_id": server_id,
            "endpoint":  server["endpoint"],
            "transport": server.get("transport", "sse"),
        })
        await on_event({
            "type":       "auth_hop",
            "from":       "agent",
            "to":         "mcp",
            "server_id":  server_id,
            "token_hint": hint,
            "token_full": token or "dev-open",
            "token_type": "jwt" if claims else "apikey",
            "sub":        claims.get("sub"),
            "roles":      claims.get("roles", []),
            "iss":        claims.get("iss"),
            "aud":        claims.get("aud"),        # should match server_id
            "exp":        claims.get("exp"),
            "key_source": key_src,
            "http_request": {
                "method":  "POST",
                "url":     server["endpoint"],
                "headers": {
                    "Authorization": f"Bearer {token or 'dev-open'}",
                    "Content-Type":  "application/json",
                    "Accept":        "application/json, text/event-stream",
                },
            },
        })

    try:
        # ── Open MCP session and discover tools (+ optionally context) ────────
        # load_mcp_tools() calls tools/list and wraps each tool definition as a
        # LangChain BaseTool for LangGraph. Tool discovery is fresh on every call
        # (no caching) because server-side tool sets can change at runtime.
        #
        # When use_context=True, _fetch_mcp_context() also discovers prompt
        # templates and resource documents and injects them into the ReAct context.
        # This adds 2–5 extra MCP round-trips but gives the LLM structured
        # domain-specific analysis frameworks and reference documentation.
        #
        # When use_context=False, only tools are loaded — simpler, faster, and
        # equivalent to the pre-context version of this agent.
        async with mcp_session(server) as session:
            tools      = await load_mcp_tools(session)
            tool_names = [t.name for t in tools]

            # ── Prompts + resources (context enrichment — optional) ───────────
            if use_context:
                prompt_messages, resource_context = await _fetch_mcp_context(
                    session, query, server_id, on_event
                )
            else:
                prompt_messages, resource_context = [], ""
                print("[mcp]  context  : disabled (use_context=False)")

            print(f"[mcp]  tools     : {', '.join(tool_names)}")

            if on_event:
                await on_event({
                    "type":           "mcp_connected",
                    "server_id":      server_id,
                    "tool_names":     tool_names,
                    "tool_count":     len(tools),
                    "prompt_count":   len(prompt_messages),
                    "has_resources":  bool(resource_context),
                })

            # ── Build system prompt + initial messages ────────────────────────
            # Base system prompt: direct the model to use tools, not invent data.
            system_prompt = (
                "You are a helpful financial assistant. "
                "Always use the available tools to retrieve data before answering. "
                "Never guess or fabricate figures — call the appropriate tool."
            )

            # If the server provided resource documents (policy rules, guides),
            # append them to the system prompt so the LLM can reference them
            # when interpreting tool results and making recommendations.
            if resource_context:
                system_prompt += (
                    "\n\n--- Reference documentation from the MCP server ---\n"
                    "Use the content below to interpret tool results, validate "
                    "compliance, and make pricing recommendations:\n\n"
                    + resource_context
                    + "\n--- End of reference documentation ---"
                )

            agent = create_react_agent(llm, tools=tools, prompt=system_prompt)
            answer = ""

            # ── Build initial messages ────────────────────────────────────────
            # If the server returned a matched prompt template, convert its
            # messages to LangChain message objects. The prompt provides a
            # structured, domain-specific analysis task that replaces the raw
            # user query — it gives the LLM an explicit step-by-step framework
            # to follow, reducing hallucination and improving completeness.
            #
            # Fallback: if no prompt matched (different server, no keyword hit),
            # use the raw user query as a plain HumanMessage — same behavior as
            # before prompts were added.
            if prompt_messages:
                initial_messages = [
                    HumanMessage(content=text) if role in ("user", "human")
                    else AIMessage(content=text)
                    for role, text in prompt_messages
                ]
                print(f"[mcp]  initial   : {len(initial_messages)} prompt message(s) (structured workflow)")
            else:
                initial_messages = [HumanMessage(content=query)]

            # ── Stream LangGraph events ───────────────────────────────────────
            # astream_events(version="v2") yields granular events as the graph
            # executes. We handle three kinds:
            #   on_tool_start     — LLM chose a tool and its args are ready
            #   on_tool_end       — MCP server returned the tool result
            #   on_chat_model_end — LLM produced output (tool decision or answer)
            #
            # IMPORTANT: the try/except below is intentionally INSIDE the
            # mcp_session context. If the LLM (Ollama) is not reachable,
            # astream_events raises ConnectError inside the MCP ClientSession's
            # internal TaskGroup. Catching it here lets the MCP session close
            # cleanly; letting it propagate causes an ExceptionGroup that is
            # harder to diagnose and produces confusing error messages.
            try:
              async for event in agent.astream_events(
                {"messages": initial_messages},
                version="v2",
              ):
                kind = event["event"]

                if kind == "on_tool_start":
                    # The LLM chose a tool to call. Log the name and args.
                    # We also construct the raw JSON-RPC request shape that the
                    # LangChain MCP adapter will send to the server — useful for
                    # the chat UI's "expand tool call" feature.
                    tool_name = event["name"]
                    args      = event["data"].get("input", {})
                    print(f"[tool] → {tool_name}  args={_fmt(args)}")
                    if on_event:
                        await on_event({
                            "type":      "tool_call",
                            "tool_name": tool_name,
                            "args":      args,
                            "server_id": server_id,
                            # The JSON-RPC body shape is what actually goes over
                            # the wire to the MCP server's tools/call endpoint.
                            "jsonrpc_request": {
                                "jsonrpc": "2.0",
                                "method":  "tools/call",
                                "params":  {"name": tool_name, "arguments": args},
                                "id":      f"call-{tool_name}",
                            },
                            "http_headers": {
                                "Authorization": f"Bearer {token or 'dev-open'}",
                                "Content-Type":  "application/json",
                                "Accept":        "application/json, text/event-stream",
                            },
                            "token_full": token or "dev-open",
                            "key_source": key_src,
                        })

                elif kind == "on_tool_end":
                    # The MCP server returned a result for the tool call.
                    # output may be a ToolMessage object (has .content) or a
                    # plain string — handle both.
                    tool_name = event["name"]
                    output    = event["data"].get("output")
                    result    = output.content if hasattr(output, "content") else str(output)
                    print(f"[tool] ← {tool_name}  result={_fmt(result)}")
                    if on_event:
                        # tool_rbac records that the RBAC check passed at the
                        # MCP server level (we received a result, not a 403).
                        # This is inferred rather than explicitly confirmed by
                        # the MCP server — if RBAC had failed, the tool call
                        # would have thrown an exception and we'd be in the
                        # except block below.
                        await on_event({
                            "type":       "tool_rbac",
                            "tool_name":  tool_name,
                            "server_id":  server_id,
                            "sub":        claims.get("sub"),
                            "roles":      claims.get("roles", []),
                            "token_hint": hint,
                            "token_type": "jwt" if claims else "apikey",
                            "key_source": key_src,
                            "result":     "PASS",
                        })
                        await on_event({
                            "type":      "tool_result",
                            "tool_name": tool_name,
                            "result":    result,
                            "server_id": server_id,
                            "jsonrpc_response": {
                                "jsonrpc": "2.0",
                                "result":  {"content": [{"type": "text", "text": result[:2000]}]},
                                "id":      f"call-{tool_name}",
                            },
                        })
                        # Detect external service calls: the pricing MCP server
                        # embeds "auth_pattern" in the tool result JSON when a
                        # tool call required calling an external HTTP service
                        # (credit bureau, FX rate, sanctions). We surface this
                        # as a separate external_tool_call event so the chat UI
                        # Security tab can show the full call chain including the
                        # MCP → external service hop.
                        if '"auth_pattern"' in result:
                            try:
                                result_data = json.loads(result)
                                await on_event({
                                    "type":             "external_tool_call",
                                    "tool_name":        tool_name,
                                    "server_id":        server_id,
                                    "external_service": result_data.get("service", "external"),
                                    "auth_pattern":     result_data.get("auth_pattern", ""),
                                    "key_source":       "tool-registry-db",
                                })
                            except Exception:
                                pass   # result wasn't valid JSON — skip the external event

                elif kind == "on_chat_model_end":
                    # The LLM finished one inference pass.
                    # This fires for BOTH intermediate outputs (tool-call decisions)
                    # and for the final textual answer. We only want to capture the
                    # final answer, which is identified by having content but no
                    # pending tool_calls.
                    #
                    # Intermediate outputs: output.tool_calls is non-empty (the
                    # LLM is instructing the agent to call a tool next).
                    # Final answer: output.tool_calls is empty (or absent) and
                    # output.content is a non-empty string — this is the answer
                    # we return to the caller.
                    output = event["data"].get("output")
                    if output is None:
                        continue
                    raw = getattr(output, "content", None)
                    if raw and not getattr(output, "tool_calls", []):
                        answer = raw if isinstance(raw, str) else str(raw)

            except Exception as _agent_exc:
                # LLM / agent-loop error — caught INSIDE the mcp_session context so
                # the MCP ClientSession can close cleanly without propagating an
                # ExceptionGroup through the session TaskGroup (which obscures the
                # real error and shows "unhandled errors in a TaskGroup" instead).
                _aexc_str   = str(_agent_exc)
                _aexc_lower = _aexc_str.lower()
                _is_llm_err = any(kw in _aexc_lower for kw in (
                    "connect", "refused", "11434", "ollama",
                    "connectionerror", "connection error",
                ))
                if _is_llm_err:
                    answer = (
                        f"LLM service ({MODEL}) is not reachable at {OLLAMA_URL}. "
                        f"Start Ollama and run: ollama pull {MODEL}"
                    )
                else:
                    answer = f"Agent error: {_aexc_str[:300]}"
                print(f"[agent] loop error ({server_id}): {_aexc_str[:200]}")
                if on_event:
                    try:
                        await on_event({"type": "error", "message": answer})
                    except Exception:
                        pass

        return answer

    except Exception as mcp_exc:
        # ── Classify and surface connection/auth errors ───────────────────────
        # Rather than propagating a raw exception message, we produce a
        # user-readable hint that points to the likely fix:
        #   401 / unauthorized  → JWT audience mismatch or wrong key
        #   connection refused  → MCP server is not running on that port
        #   other               → show the raw error for debugging
        err_str   = str(mcp_exc)
        err_lower = err_str.lower()
        is_auth   = "401" in err_str or "unauthorized" in err_lower or "authentication" in err_lower
        is_noconn = "connect" in err_lower or "refused" in err_lower or "timeout" in err_lower
        print(f"[mcp]  ERROR ({server_id}): {err_str[:200]}")
        if on_event:
            if is_auth:
                # Emit a REJECTED auth_hop so the chat UI Security tab shows
                # a red failure card with the server ID and error details.
                await on_event({
                    "type":       "auth_hop",
                    "from":       "agent",
                    "to":         "mcp",
                    "server_id":  server_id,
                    "valid":      False,
                    "token_hint": "REJECTED-401",
                    "token_type": "failed",
                    "key_source": key_src,
                    "error":      err_str[:200],
                })
            # Build a targeted hint message based on the error category.
            hint_msg = (
                f"MCP server '{server_id}' rejected auth (401). "
                f"Ensure the hub is reachable and {server_id} trusts hub-issued JWTs."
            ) if is_auth else (
                f"MCP server '{server_id}' is not reachable at {server['endpoint']}. "
                f"Start it with: {server.get('start_cmd', 'see mcp-hub.json')}"
            ) if is_noconn else f"MCP server '{server_id}' error: {err_str[:300]}"
            await on_event({"type": "error", "message": hint_msg})
        return f"Error: {err_str}"


# ─── Meta-answer fallback (no server matched) ─────────────────────────────────

async def _answer_from_hub_meta(
    query: str,
    hub_meta: dict,
    hub_token: str,
    on_event=None,
) -> str:
    """Answer hub/server information questions when /discover routes to no MCP server.

    Called when the hub's LLM router decides the query is about the hub itself
    (e.g., "What servers are available?") rather than about data one of the MCP
    servers can answer.

    Strategy:
        1. Fetch GET /servers to get full server descriptions (not just IDs).
        2. Build an LLM prompt that includes those descriptions as context.
        3. Ask the LLM to answer the query using that context.
        4. Fall back to a plain text list if the LLM call fails or is disabled.
    """
    server_info = []
    try:
        # Fetch the full server list from the hub to give the LLM rich context
        # (description, skills, capability) rather than just server IDs.
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{HUB_SERVER_URL}/servers",
                headers=_auth_headers(hub_token),
                timeout=10.0,
            )
            if r.status_code == 200:
                server_info = r.json().get("servers", [])
    except Exception as exc:
        print(f"[agent] meta-fetch /servers failed: {exc}")

    hub_name = hub_meta.get("hub_name", "FAB MCP Hub")

    if server_info:
        # Construct a context block describing each server so the LLM can
        # answer questions like "What can the pricing server do?" accurately.
        context = "\n".join(
            f"• {s['id']} — {s.get('description', s.get('capability', ''))}"
            f"  |  skills: {', '.join(s.get('skills', []))}"
            for s in server_info
        )
        prompt = (
            f"You are an assistant for the {hub_name}. "
            f"Here are the registered MCP servers:\n{context}\n\n"
            f"Answer the following question concisely: {query}"
        )
        try:
            response = await llm.ainvoke([HumanMessage(content=prompt)])
            answer = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            # LLM call failed (Ollama offline, model not pulled, etc.).
            # Fall back to a plain enumeration so the user still gets a useful reply.
            ids = [s["id"] for s in server_info]
            answer = (
                f"The {hub_name} has {len(ids)} registered MCP server(s): "
                f"{', '.join(ids)}.\n\n(LLM call failed: {exc})"
            )
    else:
        # No server list available (hub offline or auth failure on /servers).
        # Use whatever the /discover response gave us in hub_metadata.
        ids = hub_meta.get("server_ids", [])
        answer = (
            f"The {hub_name} has {len(ids)} registered server(s): "
            f"{', '.join(ids) if ids else 'none found'}.\n"
            f"No specific server was selected — try a more specific question."
        )

    print(f"[agent] meta-answer: {_fmt(answer)}")
    if on_event:
        await on_event({"type": "final_answer", "content": answer})
    return answer


# ─── Main public entry point ─────────────────────────────────────────────────

async def run_agent(
    query: str,
    on_event=None,
    hub_token: str = "",
    use_context: bool | None = None,
) -> str:
    """Orchestrate the full agent flow for one user query.

    Steps:
        1. Resolve the hub Bearer token (caller-supplied, cached login, or static key).
        2. POST /discover with the query → hub routes and returns matched servers +
           per-server RS256 JWTs.
        3. If no servers matched → fall back to _answer_from_hub_meta().
        4. If one server matched  → run _run_on_server() directly (no overhead).
        5. If multiple servers matched → asyncio.gather() across all servers in
           parallel; each server gets its own MCP session and ReAct loop.
           return_exceptions=True means one failing server doesn't abort others.

    Args:
        query:       The natural-language user query.
        on_event:    Optional async callback for streaming trace events to the
                     chat UI. Called with a dict for each event (tool_call,
                     tool_result, auth_hop, routing, final_answer, error, …).
        hub_token:   Optional caller-supplied Bearer token. The chat server passes
                     the user's session JWT here so the hub log records the real
                     user identity (sub, roles) rather than the agent identity.
                     When absent, the agent logs in via _get_hub_token().
        use_context: Whether to discover and inject MCP prompt templates and
                     resource documents before running the ReAct tool loop.
                     None (default) → use the module default set by the
                     AGENT_CONTEXT_ENABLED env var (default: True).
                     True  → always fetch and inject context (prompts + resources).
                     False → skip context; use MCP tools only (simpler, faster).
    """
    # Resolve the context flag — None means "use env var default".
    _use_context: bool = _DEFAULT_CONTEXT if use_context is None else use_context
    if not _use_context:
        print("[agent] context  : disabled — using tools-only mode")
    # Resolve the hub token — use the caller-supplied token first, then fall
    # back to the cached agent login token. This allows the chat server to pass
    # its own session JWT (which carries the human user's identity) so that
    # hub logs show "admin" or "analyst" rather than the generic agent identity.
    _hub_token = hub_token or await _get_hub_token()
    hub_claims = _decode_jwt_claims(_hub_token)   # unverified, for logging only
    hub_hint   = _token_hint(_hub_token)

    # ── Emit the agent → hub auth_hop event ──────────────────────────────────
    # This event records the exact JWT/key used for the /discover call.
    # It appears as the first hop in the chat UI's Security tab.
    if on_event:
        await on_event({
            "type":       "auth_hop",
            "from":       "agent",
            "to":         "hub",
            "token_hint": hub_hint,
            "token_full": _hub_token or "dev-open",
            "hub_url":    HUB_SERVER_URL,
            "token_type": "jwt" if hub_claims else "apikey",
            "sub":        hub_claims.get("sub"),
            "roles":      hub_claims.get("roles", []),
            "iss":        hub_claims.get("iss"),
            "exp":        hub_claims.get("exp"),
            "iat":        hub_claims.get("iat"),
        })

    # ── Step 1: POST /discover — hub routing ─────────────────────────────────
    # The hub's /discover endpoint:
    #   - Validates the Bearer JWT (RS256 or HS256, RBAC: requires 'agent' role).
    #   - Sends the query to the local LLM router to pick matching MCP servers.
    #   - Mints a short-lived per-server RS256 JWT (aud=server_id) for each match.
    #   - Returns the matched servers with their endpoints, transports, and tokens.
    #
    # timeout=120.0 is generous because the LLM routing call can take 30+ seconds
    # on CPU-only hardware. If HUB_LLM_ENABLED=false, the hub returns immediately.
    print(f"\n[hub]  query     : {query}")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{HUB_SERVER_URL}/discover",
                json={"intent": query},
                headers=_auth_headers(_hub_token),
                timeout=120.0,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        msg = f"Hub unreachable: {type(exc).__name__}: {exc}"
        print(f"[hub]  ERROR: {msg}")
        if on_event:
            await on_event({"type": "error", "message": msg})
        return msg

    # Unpack the /discover response.
    # hub_metadata     → hub name, full registered server ID list (for meta-answers).
    # hub_auth_meta    → the identity the hub recognized from our JWT (sub, roles).
    # servers          → the matched servers with endpoints, transports, and tokens.
    servers       = data.get("servers", [])
    hub_meta      = data.get("hub_metadata", {})
    hub_auth_meta = data.get("auth_meta", {})

    # ── Step 2b: INBOUND VERIFICATION — verify each per-server JWT via JWKS ──
    # The hub just returned a list of matched servers, each with a short-lived
    # RS256 JWT (aud=server_id, exp=1h). Before the agent uses any token, it
    # verifies the RS256 signature + issuer + audience against the hub's public
    # key from /.well-known/jwks.json.
    #
    # Why here, not only on the MCP server?
    #   The MCP server (FastMCP JWTVerifier) is the FINAL validator, but verifying
    #   at the agent side provides early detection: a tampered or forged token is
    #   caught before a TCP connection is even opened to the MCP server.
    #
    # Hard fail (raise) → server is SKIPPED; token cannot be trusted.
    # Soft fail (return) → JWKS unreachable; warning logged; token used anyway
    #   (MCP server remains the last line of defense via its JWTVerifier).
    valid_servers = []
    for _srv in servers:
        _server_token = _srv.get("server_token")
        _srv_id       = _srv.get("id", "?")
        if _server_token:
            try:
                _claims = await _verify_hub_token(_server_token, audience=_srv_id)
                print(
                    f"[auth] server token verified : id={_srv_id} "
                    f"aud={_claims.get('aud')} exp={_claims.get('exp')}"
                )
                valid_servers.append(_srv)
            except Exception as _ve:
                # Hard failure from _verify_hub_token — explicit crypto mismatch
                # or token expiry. Skip this server to prevent using a bad token.
                print(
                    f"[auth] SECURITY: server token for {_srv_id} FAILED JWKS "
                    f"verification ({type(_ve).__name__}: {_ve}) — server skipped"
                )
                if on_event:
                    await on_event({
                        "type":      "error",
                        "message":   (
                            f"Server token for '{_srv_id}' failed RS256 JWKS "
                            f"verification ({type(_ve).__name__}) — server skipped."
                        ),
                    })
        else:
            # No JWT — server uses api_key or MCP_API_KEY fallback; skip JWKS check
            valid_servers.append(_srv)
    servers = valid_servers

    print(f"[hub]  method    : {data['method']}")     # "llm" or "fast_path"
    print(f"[hub]  reason    : {data['reason']}")     # routing explanation
    print(f"[hub]  servers   : {[s['id'] for s in servers]}")
    if hub_auth_meta.get("sub"):
        print(
            f"[hub]  auth      : sub={hub_auth_meta['sub']} "
            f"roles={hub_auth_meta.get('roles')} type={hub_auth_meta.get('token_type')}"
        )

    # ── Emit hub_loaded and routing trace events ──────────────────────────────
    # hub_loaded → chat UI shows the hub name and full server registry.
    # routing    → chat UI shows which servers were selected, method, and reason.
    #              The full HTTP request + response shape is embedded so the
    #              chat UI's "expand" button can show what /discover actually returned.
    if on_event:
        await on_event({
            "type":       "hub_loaded",
            "hub_name":   hub_meta.get("hub_name"),
            "server_ids": hub_meta.get("server_ids", []),
            "hub_auth":   hub_auth_meta,
        })
        await on_event({
            "type":          "routing",
            "method":        data["method"],
            "reason":        data["reason"],
            "server_id":     servers[0]["id"] if servers else None,
            "server_ids":    [s["id"] for s in servers],
            "hub_token":     _hub_token or "dev-open",
            "hub_token_type": "jwt" if hub_claims else "apikey",
            "hub_sub":       hub_claims.get("sub"),
            "hub_roles":     hub_claims.get("roles", []),
            "hub_iss":       hub_claims.get("iss"),
            # Full HTTP request/response snapshot for the chat UI expand button.
            "http": {
                "request": {
                    "method":  "POST",
                    "url":     f"{HUB_SERVER_URL}/discover",
                    "headers": {
                        "Authorization": f"Bearer {_hub_token or 'dev-open'}",
                        "Content-Type":  "application/json",
                    },
                    "body": {"intent": query},
                },
                "response": {
                    "status":  resp.status_code,
                    "headers": {"Content-Type": "application/json"},
                    "body": {
                        "servers": [
                            {"id": s["id"], "endpoint": s["endpoint"],
                             "transport": s.get("transport"),
                             "server_token": s.get("server_token", "<per-server-jwt>")}
                            for s in servers
                        ],
                        "method":    data.get("method"),
                        "reason":    data.get("reason"),
                        "hub_name":  hub_meta.get("hub_name"),
                        "auth_meta": hub_auth_meta,
                    },
                },
            },
        })

    # ── Step 2: execute on matched server(s) ─────────────────────────────────
    if not servers:
        # No MCP server matched the query. This happens when:
        #   - The query is about the hub itself ("What servers do you have?")
        #   - The routing LLM couldn't find a relevant server
        #   - HUB_LLM_ENABLED=false and no server matched the fast-path rules
        # Fall back to answering from hub metadata rather than returning an error.
        print("[hub]  no server matched — attempting meta-answer from hub info")
        return await _answer_from_hub_meta(query, hub_meta, _hub_token, on_event)

    try:
        if len(servers) == 1:
            # Single server — run directly without the asyncio.gather overhead.
            answer = await _run_on_server(servers[0], query, on_event,
                                          use_context=_use_context)
        else:
            # Multiple servers matched (e.g., "Comprehensive analysis of CUST001"
            # may select both Customer Intelligence and Pricing servers).
            # Run them in parallel — each server gets its own MCP session and
            # independent ReAct loop.
            #
            # return_exceptions=True: if one server fails (offline, 401, etc.),
            # asyncio.gather still returns results for the other servers instead
            # of raising an exception that would discard all successful results.
            print(f"[agent] parallel execution across {len(servers)} servers")
            results = await asyncio.gather(
                *[_run_on_server(s, query, on_event, use_context=_use_context)
                  for s in servers],
                return_exceptions=True,
            )
            # Combine per-server answers into one response, labelled by server ID.
            # Exceptions are shown inline so partial results are still visible.
            parts = []
            for server, result in zip(servers, results):
                if isinstance(result, Exception):
                    parts.append(f"[{server['id']}]\nError: {type(result).__name__}: {result}")
                else:
                    parts.append(f"[{server['id']}]\n{result}")
            answer = "\n\n".join(parts)
    except Exception as exc:
        msg = f"Error: {type(exc).__name__}: {exc}"
        print(f"[agent] ERROR: {msg}")
        if on_event:
            await on_event({"type": "error", "message": msg})
        return msg

    print(f"[agent] answer    : {_fmt(answer)}")
    if on_event:
        await on_event({"type": "final_answer", "content": answer})
    return answer


# ─── CLI entry point ─────────────────────────────────────────────────────────

def main() -> None:
    """Run the agent from the command line.

    Usage (with prompts + resources — default):
        python agent.py "analyze pricing for CUST001"
        python agent.py --context "show me the 360 profile for CUST002"

    Usage (tools only — simpler, fewer round-trips):
        python agent.py --no-context "analyze pricing for CUST001"
        AGENT_CONTEXT_ENABLED=false python agent.py "..."

    Env var overrides:
        AGENT_CONTEXT_ENABLED=false   → always run without context (tools only)
        AGENT_CONTEXT_ENABLED=true    → always run with context (default)
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="FAB MCP Hub Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "query",
        nargs="+",
        help="Natural-language query (multi-word; no quotes needed)",
    )

    ctx_group = parser.add_mutually_exclusive_group()
    ctx_group.add_argument(
        "--context",
        dest="use_context",
        action="store_true",
        default=None,
        help=(
            "Inject MCP prompt templates and resource documents into the "
            "ReAct context (default; overrides AGENT_CONTEXT_ENABLED=false)"
        ),
    )
    ctx_group.add_argument(
        "--no-context",
        dest="use_context",
        action="store_false",
        help=(
            "Skip MCP prompts and resources — use tools only. "
            "Faster, simpler, fewer MCP round-trips. "
            "Overrides AGENT_CONTEXT_ENABLED=true."
        ),
    )

    args = parser.parse_args()

    # Resolve use_context: explicit flag beats env var; None means "use env var default".
    use_context: bool | None = args.use_context  # True, False, or None

    query = " ".join(args.query)
    result = asyncio.run(run_agent(query, use_context=use_context))
    print(result)


if __name__ == "__main__":
    main()
