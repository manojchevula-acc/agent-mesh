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
import sys
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
from langchain_core.messages import HumanMessage
from langgraph.prebuilt import create_react_agent

# ─── Configuration constants ─────────────────────────────────────────────────
OLLAMA_URL     = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
MODEL          = os.environ.get("OLLAMA_MODEL",    "llama3.2:3b")
HUB_SERVER_URL = os.environ.get("HUB_SERVER_URL",  "http://localhost:8090")

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


# ─── Hub JWT login ───────────────────────────────────────────────────────────
# The hub token is cached in a module-level variable for the process lifetime.
# Re-using the same JWT across calls avoids a round-trip login on every query.
# The token's 24-hour (default) expiry is much longer than a typical agent
# process life, so a simple in-memory cache is sufficient here.
#
# For long-running services (like the chat server) that call run_agent()
# repeatedly over hours, the token may expire. In that case the chat server
# supplies its own hub_token per call via the hub_token parameter on run_agent().
_hub_token_cache: str = ""


async def _get_hub_token() -> str:
    """Login to the hub and return an RS256 JWT; cache for the process lifetime.

    Token acquisition order:
        1. Return the in-process cache if already populated.
        2. POST /auth/login with HUB_AGENT_USERNAME + HUB_AGENT_PASSWORD.
        3. Fall back to HUB_API_KEY (static pre-shared key) if login fails or
           credentials are not set.
    """
    global _hub_token_cache
    if _hub_token_cache:
        # Already logged in — reuse the cached JWT for this process.
        return _hub_token_cache

    if _HUB_AGENT_USERNAME and _HUB_AGENT_PASSWORD:
        # Preferred path: exchange credentials for a hub-issued RS256 JWT.
        # The response carries "access_token" (the JWT) plus sub, roles, expiry.
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{HUB_SERVER_URL}/auth/login",
                    json={"username": _HUB_AGENT_USERNAME, "password": _HUB_AGENT_PASSWORD},
                    timeout=10.0,
                )
                resp.raise_for_status()
                _hub_token_cache = resp.json()["access_token"]
                print(f"[hub]  login     : sub={_HUB_AGENT_USERNAME} (RS256 JWT)")
                return _hub_token_cache
        except Exception as exc:
            # Login failed (wrong credentials, hub offline, etc.).
            # Log and fall through to the static key fallback so the agent
            # can still attempt to run rather than aborting immediately.
            print(f"[hub]  login failed ({exc}), falling back to HUB_API_KEY")

    # Fallback: use the static HUB_API_KEY. If this is also empty, the hub
    # is in open-dev mode and will accept any (or no) token.
    _hub_token_cache = HUB_API_KEY
    return _hub_token_cache


# ─── MCP session context manager ─────────────────────────────────────────────
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


def _decode_jwt_claims(token: str) -> dict:
    """Decode JWT payload claims WITHOUT verifying the signature.

    This is intentionally unverified — we already trust the token because
    we either issued it ourselves (hub login) or received it from the hub
    (per-server token in /discover response). The purpose here is purely
    observability: extracting sub, roles, aud, exp, iss so they can be
    logged in auth_hop events and displayed in the chat UI Security tab.

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


# ─── Per-server ReAct execution ──────────────────────────────────────────────

async def _run_on_server(server: dict, query: str, on_event=None) -> str:
    """Connect to one MCP server, discover its tools, run a ReAct loop, return the answer.

    This function is the core of the agent's tool-use loop:
      1. Open an authenticated MCP session (SSE or streamable-HTTP).
      2. Call load_mcp_tools() — discovers all tools the server exposes and wraps
         them as LangChain-compatible Tool objects.
      3. create_react_agent() builds a LangGraph ReAct graph:
         - LLM decides which tool to call and with what arguments.
         - Tool is executed via the MCP session.
         - LLM receives the tool result and decides to call another tool or answer.
         - Loop continues until the LLM emits a final text response.
      4. astream_events(version="v2") streams fine-grained events:
         - on_tool_start  → agent is invoking a tool
         - on_tool_end    → tool returned a result
         - on_chat_model_end → LLM produced output (tool decision or final answer)

    on_event(event_dict) — optional async callback for streaming events to the
    chat UI trace panel. Events are fire-and-forget from this function's
    perspective; the caller (chat_server.py) queues them into the SSE stream.
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
        # ── Open MCP session and discover tools ───────────────────────────────
        # load_mcp_tools() calls tools/list on the MCP server and converts each
        # tool definition into a LangChain BaseTool so LangGraph can invoke them.
        # Tool discovery is done fresh on every call — no caching — because
        # server-side tool sets can change between deployments.
        async with mcp_session(server) as session:
            tools      = await load_mcp_tools(session)
            tool_names = [t.name for t in tools]
            print(f"[mcp]  tools     : {', '.join(tool_names)}")

            if on_event:
                # mcp_connected signals the chat UI that the tool list is ready
                # and shows the discovered tool names in the Timeline tab.
                await on_event({
                    "type":       "mcp_connected",
                    "server_id":  server_id,
                    "tool_names": tool_names,
                    "tool_count": len(tools),
                })

            # ── Build the ReAct agent graph ───────────────────────────────────
            # create_react_agent returns a LangGraph CompiledGraph that implements
            # the Reasoning + Acting (ReAct) pattern:
            #   THINK → tool call → observe result → THINK → … → final answer
            #
            # The system prompt keeps the model focused on using tools rather
            # than making up data. Without it, some models skip tool calls and
            # hallucinate answers directly.
            agent  = create_react_agent(
                llm,
                tools=tools,
                prompt="You are a helpful assistant. Use the available tools to answer the user's question.",
            )
            answer = ""

            # ── Stream LangGraph events ───────────────────────────────────────
            # astream_events(version="v2") yields a stream of granular events
            # as the graph executes. "v2" is the current LangGraph event API
            # version; "v1" is deprecated and has a different event schema.
            #
            # We only care about three event kinds:
            #   on_tool_start     — LLM decided to call a tool; args are ready
            #   on_tool_end       — tool returned a result
            #   on_chat_model_end — LLM produced output (either a tool-call
            #                       decision or the final textual answer)
            async for event in agent.astream_events(
                {"messages": [HumanMessage(content=query)]},
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

async def run_agent(query: str, on_event=None, hub_token: str = "") -> str:
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
        query:      The natural-language user query.
        on_event:   Optional async callback for streaming trace events to the
                    chat UI. Called with a dict for each event (tool_call,
                    tool_result, auth_hop, routing, final_answer, error, …).
        hub_token:  Optional caller-supplied Bearer token. The chat server passes
                    the user's session JWT here so the hub log records the real
                    user identity (sub, roles) rather than the agent identity.
                    When absent, the agent logs in via _get_hub_token().
    """
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
            answer = await _run_on_server(servers[0], query, on_event)
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
                *[_run_on_server(s, query, on_event) for s in servers],
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
    """Run the agent from the command line: python agent.py <query>"""
    if len(sys.argv) < 2:
        print("Usage: python agent.py <query>")
        sys.exit(1)
    # Join all remaining args as the query so multi-word queries don't need quotes.
    result = asyncio.run(run_agent(" ".join(sys.argv[1:])))
    print(result)


if __name__ == "__main__":
    main()
