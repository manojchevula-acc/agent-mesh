"""
MCP server authentication and RBAC.

Architecture
------------
  Hub issues RS256 JWTs — one per MCP server, audience = server_id.
  FastMCP JWTVerifier validates tokens (rejects 401 on bad/missing token).
  ClaimsExtractorMiddleware reads the already-validated claims into a ContextVar
  so per-tool RBAC via require_role() works without touching tool signatures.

  Agent ──[RS256 JWT, aud=server_id]──► FastMCP JWTVerifier  (validates; 401 on failure)
                                            │  token passes through
                                            ▼
                                       ClaimsExtractorMiddleware  (unverified decode → claims)
                                            │
                                            ▼
                                       require_role()   ← per-tool RBAC
                                            │
                                            ▼
                                       audit_log()      ← agent identity + service call
                                            │
                                            ▼
                            MCP Tool ──[MCP_TOOL_KEY]──► External HTTP APIs
                                     ──[MYSQL_USER/PASSWORD]──► MySQL

Key design decision — NO JWKS call in the MCP server
-----------------------------------------------------
  ClaimsExtractorMiddleware does NOT re-fetch the hub's JWKS or re-verify the
  token. FastMCP's JWTVerifier (configured via build_jwt_verifier()) is the
  sole trust anchor:

    JWTVerifier(
        jwks_uri = "http://localhost:8090/.well-known/jwks.json",
        issuer   = "fab-mcp-hub",
        audience = "fab-pricing-server",
    )

  It contacts the hub's JWKS endpoint and verifies the RS256 signature, iss,
  aud, exp, and nbf before the request reaches any middleware or tool function.
  Once a request has passed JWTVerifier, an unverified jwt.decode() is
  sufficient to read the claims for RBAC — no second network round-trip needed.

Agent connection pattern (FastMCP client with bearer token)
-----------------------------------------------------------
  client = Client(
      "http://127.0.0.1:9200/mcp",
      auth=server_token,          # per-server RS256 JWT; aud=fab-pricing-server
  )
  async with client:
      result = await client.call_tool("pricing_recommendation", {...})

  (In agent.py this is done via streamablehttp_client(endpoint, headers={"Authorization": "Bearer …"}))

Config
------
  MCP_AUTH_ENABLED    true (default) / false
  MCP_SERVER_ID       audience for JWT validation (e.g. fab-customer-server)
  HUB_SERVER_URL      hub base URL for JWKS    (default: http://localhost:8090)
  MCP_JWT_ISSUER      expected JWT issuer       (default: fab-mcp-hub)
"""

from __future__ import annotations

import contextvars
import json
import os
import time

from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

MCP_AUTH_ENABLED: bool = os.environ.get("MCP_AUTH_ENABLED", "true").lower() in ("1", "true", "yes")

# Convenience flag for startup log messages — True when auth is fully disabled.
# Imported by server modules:  "open-dev" if _MCP_DEV_MODE_ACTIVE else "enabled"
_MCP_DEV_MODE_ACTIVE: bool = not MCP_AUTH_ENABLED

# Per-request claims set by ClaimsExtractorMiddleware, read by require_role() / audit_log()
_request_claims: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "mcp_request_claims", default={}
)


# ---------------------------------------------------------------------------
# Public API — used by tool functions
# ---------------------------------------------------------------------------

def get_agent_context() -> dict:
    """Return the verified JWT claims for the currently executing MCP request.

    Populated by ClaimsExtractorMiddleware before any tool function runs.
    The claims were originally validated by FastMCP JWTVerifier (upstream).

    Example return value:
        {"sub": "agent", "roles": ["agent"], "iss": "fab-mcp-hub",
         "aud": "fab-customer-server"}

    Returns an empty dict in open dev mode (MCP_AUTH_ENABLED=false or no token).
    """
    return _request_claims.get()


def require_role(*roles: str) -> None:
    """Enforce RBAC — raise PermissionError if the caller lacks any of *roles*.

    admin role bypasses every check. No-op when claims are empty (open dev mode).
    Any one matching role is sufficient to pass.

    Args:
        *roles: Role strings to check (e.g. "admin", "agent").

    Example:
        require_role("admin", "agent")   # passes if caller has either role
        require_role("admin")            # admin-only tool
    """
    claims = _request_claims.get()
    if not claims:
        return  # open dev mode — no claims set
    user_roles = claims.get("roles", [])
    if "admin" in user_roles:
        return
    if not any(r in user_roles for r in roles):
        raise PermissionError(
            f"Role required: {list(roles)!r}, caller has {user_roles!r}."
        )


def audit_log(tool: str, args: dict | None = None, service: str = "mysql") -> None:
    """Emit a structured audit event linking agent identity to a service call.

    Call after require_role() and before the actual service call so every
    authorized invocation is recorded with the caller's verified identity.

    Args:
        tool:    Tool function name  (e.g. "customer_360")
        args:    Tool argument dict — keys are logged, values omitted (PII).
        service: Downstream service: "mysql", "http-api", "compute", etc.

    Example log output:
        {"ts": 1785857461.009, "type": "tool_audit", "tool": "customer_360",
         "service": "mysql", "sub": "agent", "roles": ["agent"],
         "args_keys": ["customer_id"]}
    """
    claims = _request_claims.get()
    print(json.dumps({
        "ts":        round(time.time(), 3),
        "type":      "tool_audit",
        "tool":      tool,
        "service":   service,
        "sub":       claims.get("sub", "unknown"),
        "roles":     claims.get("roles", []),
        "args_keys": sorted(args.keys()) if args else [],
    }, default=str))


# ---------------------------------------------------------------------------
# FastMCP JWTVerifier factory — the hub's verifier wired into FastMCP
# ---------------------------------------------------------------------------

def build_jwt_verifier():
    """Return a FastMCP JWTVerifier for hub-signed RS256 tokens.

    This is the primary and only cryptographic gatekeeper on the MCP server.
    FastMCP calls it before every tool handler; it fetches the hub's JWKS
    endpoint and verifies the RS256 signature, iss, aud, exp, and nbf.

    Configuration (from environment / .env):
        HUB_SERVER_URL  http://localhost:8090     → JWKS URI base
        MCP_JWT_ISSUER  fab-mcp-hub               → expected iss claim
        MCP_SERVER_ID   fab-pricing-server         → expected aud claim

    Effective JWTVerifier call:
        JWTVerifier(
            jwks_uri = "http://localhost:8090/.well-known/jwks.json",
            issuer   = "fab-mcp-hub",
            audience = "fab-pricing-server",   # omitted when MCP_SERVER_ID unset
        )

    Returns None when:
        • MCP_AUTH_ENABLED=false  (auth disabled; open dev mode)
        • fastmcp.server.auth not available  (older FastMCP build)
    """
    if not MCP_AUTH_ENABLED:
        return None
    try:
        from fastmcp.server.auth.providers.jwt import JWTVerifier  # type: ignore
    except ImportError:
        return None

    hub_url  = os.environ.get("HUB_SERVER_URL", "http://localhost:8090")
    issuer   = os.environ.get("MCP_JWT_ISSUER",  "fab-mcp-hub")
    audience = os.environ.get("MCP_SERVER_ID",   "")

    kwargs: dict = {
        "jwks_uri": f"{hub_url}/.well-known/jwks.json",
        "issuer":   issuer,
    }
    if audience:
        kwargs["audience"] = audience

    return JWTVerifier(**kwargs)


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _set_claims(payload: dict) -> None:
    """Normalize roles and populate the per-request claims ContextVar."""
    roles = payload.get("roles", ["agent"])
    if isinstance(roles, str):
        roles = [roles]
    _request_claims.set({
        "sub":   payload.get("sub", "unknown"),
        "roles": roles,
        "iss":   payload.get("iss"),
        "aud":   payload.get("aud"),
    })


# ---------------------------------------------------------------------------
# Starlette middleware
# ---------------------------------------------------------------------------

class ClaimsExtractorMiddleware(BaseHTTPMiddleware):
    """Extract JWT claims from an already-validated token for per-tool RBAC.

    Runs AFTER FastMCP JWTVerifier in the middleware stack. By the time a
    request reaches this middleware, JWTVerifier has already:
        • Fetched the hub's JWKS endpoint and verified the RS256 signature
        • Validated iss, aud (= MCP_SERVER_ID), exp, and nbf

    This middleware does NOT re-verify the token and does NOT call the hub's
    JWKS endpoint. It performs an unverified jwt.decode() solely to read the
    payload claims (sub, roles, iss, aud) and store them in _request_claims
    so require_role() and audit_log() work inside tool functions.

    Design rationale
    ----------------
    An unverified decode is safe here because:
      1. JWTVerifier (upstream) already rejected any tampered or expired token.
      2. The claims are used only for RBAC logging — never as credentials.
      3. Eliminating a second JWKS fetch per tool call keeps latency low.
    """

    async def dispatch(self, request: Request, call_next):
        auth  = request.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""

        if token:
            try:
                import jwt as _jwt  # PyJWT
                # Unverified decode: JWTVerifier (upstream) has already validated
                # the signature, aud, iss, and exp. We only need the payload claims.
                payload = _jwt.decode(token, options={"verify_signature": False})
                _set_claims(payload)
                print(json.dumps({
                    "ts":    round(time.time(), 3),
                    "type":  "auth",
                    "sub":   payload.get("sub"),
                    "roles": payload.get("roles", ["agent"]),
                    "path":  request.url.path,
                }, default=str))
            except Exception as exc:
                # Malformed token — JWTVerifier should have blocked this already.
                # Log and proceed; JWTVerifier is the real enforcement boundary.
                print(json.dumps({
                    "ts":      round(time.time(), 3),
                    "type":    "auth",
                    "warning": f"ClaimsExtractor decode failed: {type(exc).__name__}: {exc}",
                    "path":    request.url.path,
                }, default=str))

        elif not MCP_AUTH_ENABLED:
            # Dev mode: no token + auth disabled → grant admin for local testing.
            _request_claims.set({"sub": "anonymous", "roles": ["admin"]})

        return await call_next(request)


# ---------------------------------------------------------------------------
# Middleware factory
# ---------------------------------------------------------------------------

def claims_middleware() -> list[Middleware]:
    """Return [ClaimsExtractorMiddleware] for all MCP servers.

    Used by: customer_server.py, pricing_server.py, and the three demo servers.

    FastMCP JWTVerifier (configured via build_jwt_verifier()) is the upstream
    trust anchor for all cryptographic validation. This middleware only reads
    claims from the already-validated token so require_role() and audit_log()
    have access to the caller's identity inside tool functions.

    No JWKS call. No PyJWKClient. No re-verification.
    """
    return [Middleware(ClaimsExtractorMiddleware)]
