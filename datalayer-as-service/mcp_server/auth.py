"""
MCP server authentication and RBAC.

Architecture
------------
  Hub issues RS256 JWTs — one per MCP server, audience = server_id.
  FastMCP JWTVerifier validates tokens (rejects 401 on bad/missing token).
  BearerClaimsMiddleware extracts claims into a ContextVar so per-tool
  RBAC via require_role() works without touching tool signatures.

  Agent ──[RS256 JWT, aud=server_id]──► FastMCP JWTVerifier (rejects)
                                            │  validated
                                            ▼
                                       BearerClaimsMiddleware → _request_claims ContextVar
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

Config
------
  MCP_AUTH_ENABLED    true (default) / false
  MCP_SERVER_ID       audience for JWT validation (e.g. fab-customer-server)
  HUB_SERVER_URL      hub base URL for JWKS    (default: http://localhost:8090)
  MCP_JWT_ISSUER      expected JWT issuer       (default: fab-mcp-hub)
  MCP_TOOL_KEY        key for MCP → external HTTP service calls
"""

from __future__ import annotations

import contextvars
import json
import os
import time

from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

MCP_AUTH_ENABLED: bool = os.environ.get("MCP_AUTH_ENABLED", "true").lower() in ("1", "true", "yes")

# Per-request claims set by BearerClaimsMiddleware, read by require_role() / audit_log()
_request_claims: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "mcp_request_claims", default={}
)


# ---------------------------------------------------------------------------
# Public API — used by tool functions
# ---------------------------------------------------------------------------

def get_current_claims() -> dict:
    """Return JWT claims for the currently executing MCP request."""
    return _request_claims.get()


get_agent_context = get_current_claims  # alias kept for compatibility


def require_role(*roles: str) -> None:
    """Enforce RBAC — raise PermissionError if the caller lacks all of *roles*.

    admin role bypasses every check. No-op when auth is disabled or claims
    are empty (open dev mode).

    Args:
        *roles: Any one matching role is sufficient to pass.
    """
    claims = _request_claims.get()
    if not claims:
        return  # open dev mode
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


def service_auth_headers(service: str = "default") -> dict[str, str]:
    """Return Authorization header for MCP → external HTTP service calls.

    Uses MCP_TOOL_KEY — the MCP server's own credential, completely
    separate from the agent JWT which is never forwarded downstream.
    """
    key = os.environ.get("MCP_TOOL_KEY", os.environ.get("MCP_SERVICE_TOKEN", ""))
    return {"Authorization": f"Bearer {key}"} if key else {}


# ---------------------------------------------------------------------------
# FastMCP JWTVerifier factory
# ---------------------------------------------------------------------------

def build_jwt_verifier():
    """Return a FastMCP JWTVerifier for hub-signed RS256 tokens, or None if
    auth is disabled / FastMCP version does not support it."""
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
# Starlette middleware
# ---------------------------------------------------------------------------

class BearerClaimsMiddleware(BaseHTTPMiddleware):
    """Extract JWT claims from Bearer token into ContextVar for RBAC.

    Token validity is enforced by FastMCP's JWTVerifier (or the legacy
    BearerAuthMiddleware below). This middleware only decodes without
    re-verifying — it runs AFTER JWTVerifier in the middleware stack, so
    any token that reaches here has already been RS256-verified and its
    signature trusted. The verify_signature=False decode is therefore safe:
    it avoids a redundant JWKS fetch on every request while still extracting
    the claims required by require_role() inside tool functions.

    Execution order in the FastMCP middleware stack:
        1. JWTVerifier  — validates RS256 signature, aud, iss, exp  → 401 if invalid
        2. BearerClaimsMiddleware  — decodes (no re-verify) → sets _request_claims
        3. Tool function  — calls require_role() which reads _request_claims
    """

    async def dispatch(self, request: Request, call_next):
        auth  = request.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
        if token:
            try:
                import jwt  # PyJWT
                # verify_signature=False: token was already RS256-verified by JWTVerifier
                # upstream. Skipping re-verification avoids a redundant JWKS HTTP fetch
                # per request. Never use this shortcut outside a post-verification context.
                payload = jwt.decode(token, options={"verify_signature": False})
                roles = payload.get("roles", ["agent"])
                if isinstance(roles, str):
                    roles = [roles]
                _request_claims.set({
                    "sub":   payload.get("sub", "unknown"),
                    "roles": roles,
                    "iss":   payload.get("iss"),
                    "aud":   payload.get("aud"),
                })
                print(json.dumps({
                    "ts":    round(time.time(), 3),
                    "type":  "auth",
                    "sub":   payload.get("sub"),
                    "roles": roles,
                    "path":  request.url.path,
                }, default=str))
            except Exception:
                pass  # JWTVerifier / BearerAuthMiddleware will reject invalid tokens
        return await call_next(request)


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Full JWT validation middleware for servers that do not use JWTVerifier.

    Used by demo SSE servers (calc, weather, data). Validates the RS256 JWT
    against the hub's JWKS, sets _request_claims, and rejects 401 on failure.
    """

    async def dispatch(self, request: Request, call_next):
        auth  = request.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
        valid, claims = _verify_jwt(token)

        if valid:
            _request_claims.set(claims)

        print(json.dumps({
            "ts":    round(time.time(), 3),
            "type":  "auth",
            "valid": valid,
            "sub":   claims.get("sub", "unknown"),
            "roles": claims.get("roles", []),
            "path":  request.url.path,
        }, default=str))

        if not valid:
            return Response(
                content=json.dumps({
                    "jsonrpc": "2.0",
                    "error":   {"code": -32600, "message": "Unauthorized — provide a valid Bearer JWT"},
                    "id":      None,
                }),
                status_code=401,
                media_type="application/json",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)


# ---------------------------------------------------------------------------
# Middleware factory helpers
# ---------------------------------------------------------------------------

def claims_middleware() -> list[Middleware]:
    """Claims-only middleware for servers using FastMCP JWTVerifier."""
    return [Middleware(BearerClaimsMiddleware)]


def mcp_middleware() -> list[Middleware]:
    """Full-validation middleware for demo/SSE servers without JWTVerifier.

    Returns an empty list when MCP_AUTH_ENABLED=false, leaving the server
    completely open. This is intentional for local dev only — in any other
    environment MCP_AUTH_ENABLED must be true (the default).
    """
    if not MCP_AUTH_ENABLED:
        return []
    return [Middleware(BearerAuthMiddleware)]


# ---------------------------------------------------------------------------
# JWT verification (used by BearerAuthMiddleware)
# ---------------------------------------------------------------------------

def _verify_jwt(token: str) -> tuple[bool, dict]:
    """Validate an RS256 JWT against the hub's JWKS endpoint."""
    if not MCP_AUTH_ENABLED:
        return True, {"sub": "anonymous", "roles": ["admin"]}
    if not token:
        # Dev-mode implicit admin: when NO token is provided AND MCP_SERVER_ID is
        # not set, treat the caller as an admin. This makes local curl/browser
        # testing frictionless without any JWT setup.
        # WARNING: MCP_SERVER_ID must be set in all non-dev environments.
        # Without it, any unauthenticated caller gets admin access.
        if not os.environ.get("MCP_SERVER_ID"):
            return True, {"sub": "dev", "roles": ["admin"]}
        return False, {"_error": "no_token"}

    try:
        import jwt  # PyJWT
        from jwt import PyJWKClient  # type: ignore
    except ImportError:
        return False, {"_error": "jwt_unavailable"}

    hub_url  = os.environ.get("HUB_SERVER_URL", "http://localhost:8090")
    issuer   = os.environ.get("MCP_JWT_ISSUER",  "fab-mcp-hub")
    audience = os.environ.get("MCP_SERVER_ID",   "")

    try:
        signing_key = PyJWKClient(f"{hub_url}/.well-known/jwks.json").get_signing_key_from_jwt(token)
        kwargs: dict = {"algorithms": ["RS256"], "issuer": issuer}
        if audience:
            kwargs["audience"] = audience
        else:
            kwargs["options"] = {"verify_aud": False}
        payload = jwt.decode(token, signing_key.key, **kwargs)
        roles = payload.get("roles", ["agent"])
        if isinstance(roles, str):
            roles = [roles]
        return True, {
            "sub":   payload.get("sub", "unknown"),
            "roles": roles,
            "iss":   payload.get("iss"),
            "aud":   payload.get("aud"),
        }
    except Exception as exc:
        err_type = type(exc).__name__
        if "Expired" in err_type:
            return False, {"_error": "token_expired"}
        return False, {"_error": "token_invalid"}
