"""
MCP server authentication and RBAC.

Architecture
------------
  Hub issues RS256 JWTs — one per MCP server, audience = server_id.
  FastMCP JWTVerifier validates tokens (rejects 401 on bad/missing token).
  BearerClaimsMiddleware extracts claims into a ContextVar so per-tool
  RBAC via require_role() works without touching tool signatures.

  Agent ──[RS256 JWT, aud=server_id]──► FastMCP JWTVerifier  (rejects 401)
                                            │  validated
                                            ▼
                                       BearerClaimsMiddleware (JWKS re-verify + claims)
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

Middleware verification tiers
------------------------------
  BearerClaimsMiddleware (used with FastMCP JWTVerifier):
    HARD fail (→ 401) — signature/audience/issuer mismatch, token expired
    SOFT fail (warn + proceed) — JWKS endpoint unreachable; JWTVerifier is the
      upstream gatekeeper and TLS provides transport integrity.

  BearerAuthMiddleware (stand-alone servers without JWTVerifier):
    Always full JWKS verification; no soft path — any failure → 401.

Config
------
  MCP_AUTH_ENABLED    true (default) / false
  MCP_SERVER_ID       audience for JWT validation (e.g. fab-customer-server)
  HUB_SERVER_URL      hub base URL for JWKS    (default: http://localhost:8090)
  MCP_JWT_ISSUER      expected JWT issuer       (default: fab-mcp-hub)
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
import time

from starlette.datastructures import Headers as _StarletteHeaders
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

MCP_AUTH_ENABLED: bool = os.environ.get("MCP_AUTH_ENABLED", "true").lower() in ("1", "true", "yes")

# Convenience flag for startup log messages — True when auth is fully disabled.
# Imported by server.py:  "open-dev" if _MCP_DEV_MODE_ACTIVE else "enabled"/"disabled"
_MCP_DEV_MODE_ACTIVE: bool = not MCP_AUTH_ENABLED

# Per-request claims set by middleware, read by require_role() / audit_log()
_request_claims: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "mcp_request_claims", default={}
)

# ---------------------------------------------------------------------------
# Module-level JWKS client — shared across all requests, keys cached 5 min.
# Using a single instance avoids a new PyJWKClient (and HTTP fetch) per request.
# ---------------------------------------------------------------------------

_jwks_client: object | None = None


def _get_jwks_client():
    """Lazy-init the module-level cached PyJWKClient for the hub JWKS endpoint.

    Created once on first call; public keys are cached for 5 minutes (lifespan=300)
    before re-fetching. A single shared instance avoids an HTTP round-trip on
    every incoming MCP request.

    JWKS endpoint called:
        GET http://localhost:8090/.well-known/jwks.json   (env: HUB_SERVER_URL)

    Example JWKS response:
        {
          "keys": [{
            "kty": "RSA",
            "kid": "hub-rsa-1",      ← matched against incoming JWT header "kid"
            "use": "sig",
            "alg": "RS256",
            "n":   "0vx7agoebGcQSuuPiLJXZptN9nndrQmbXEps2...",   ← RSA modulus
            "e":   "AQAB"                                          ← RSA exponent
          }]
        }

    Returns None when PyJWT ≥2.4 is not installed; callers fall back to a
    verify_signature=False decode and log a WARNING.
    """
    global _jwks_client
    if _jwks_client is None:
        try:
            from jwt import PyJWKClient  # type: ignore
            hub_url = os.environ.get("HUB_SERVER_URL", "http://localhost:8090")
            _jwks_client = PyJWKClient(
                f"{hub_url}/.well-known/jwks.json",
                cache_keys=True,
                lifespan=300,   # re-fetch public keys every 5 minutes
            )
        except ImportError:
            pass  # PyJWT not installed; callers fall back to unverified decode
    return _jwks_client


# ---------------------------------------------------------------------------
# Public API — used by tool functions
# ---------------------------------------------------------------------------

def get_agent_context() -> dict:
    """Return the verified JWT claims for the currently executing MCP request.

    Populated by BearerClaimsMiddleware / BearerAuthMiddleware before any tool
    function runs. Tool functions read this via require_role() and audit_log();
    db.py reads it for audit-trail logging.

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
# FastMCP JWTVerifier factory
# ---------------------------------------------------------------------------

def build_jwt_verifier():
    """Return a FastMCP JWTVerifier for hub-signed RS256 tokens.

    Implements the standard JWKS verification flow (steps 1-6) via FastMCP's
    built-in JWTVerifier, which runs before every tool handler.

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

    The verifier checks on every HTTP request (steps 5-6 of standard flow):
        Step 5 — RS256 signature verified against RSA public key from JWKS
        Step 6 — iss == "fab-mcp-hub"
                 aud == "fab-pricing-server"   (when MCP_SERVER_ID is set)
                 exp is in the future
                 nbf is in the past            (when claim present in token)

    Returns None when:
        • MCP_AUTH_ENABLED=false  (auth disabled; open dev mode)
        • fastmcp.server.auth not available  (older FastMCP build)
    In that case BearerClaimsMiddleware provides the fallback verification.
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

def _reject_401(reason: str = "JWT verification failed") -> Response:
    """Return a JSON-RPC 2.0-compliant 401 Unauthorized response."""
    return Response(
        content=json.dumps({
            "jsonrpc": "2.0",
            "error":   {"code": -32600, "message": f"Unauthorized — {reason}"},
            "id":      None,
        }),
        status_code=401,
        media_type="application/json",
        headers={"WWW-Authenticate": "Bearer"},
    )


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

class BearerClaimsMiddleware(BaseHTTPMiddleware):
    """[DIRECTION 2 — INBOUND VERIFICATION] Verify RS256 JWT via JWKS + set claims ContextVar.

    Used by: claims_middleware() → customer_server.py, pricing_server.py
    Runs after FastMCP JWTVerifier in the middleware stack (defense-in-depth).

    Standard JWKS verification flow:
        Step 1 — Extract JWT header: kid="hub-rsa-1", alg="RS256"
        Step 2 — Fetch JWKS: GET http://localhost:8090/.well-known/jwks.json
        Step 3 — Match key by kid: selects {"kid": "hub-rsa-1", "kty": "RSA", ...}
        Step 4 — Build RSA public key from JWK n/e fields
                 (Steps 1-4 done by PyJWKClient.get_signing_key_from_jwt)
        Step 5 — Verify RS256 signature with the public key
        Step 6 — Validate claims: iss, aud (env: MCP_SERVER_ID), exp, nbf
                 (Steps 5-6 done by jwt.decode)

    After verification, claims are stored for per-tool RBAC:
        _request_claims → {"sub": "agent", "roles": ["agent"],
                            "iss": "fab-mcp-hub", "aud": "fab-pricing-server"}

    Failure tiers
    -------------
      HARD (→ 401) : InvalidSignatureError / InvalidAudienceError /
                     InvalidIssuerError / ExpiredSignatureError
      SOFT (→ warn): JWKS endpoint unreachable — falls back to unverified decode;
                     JWTVerifier upstream is the primary gatekeeper.
      NO CLIENT    : PyJWT not installed → unverified decode (dev only).
    """

    async def dispatch(self, request: Request, call_next):
        auth  = request.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""

        if not token:
            return await call_next(request)

        import jwt as _jwt  # PyJWT

        issuer   = os.environ.get("MCP_JWT_ISSUER", "fab-mcp-hub")
        audience = os.environ.get("MCP_SERVER_ID", "")
        client   = _get_jwks_client()
        payload: dict | None = None

        if client is not None:
            try:
                # Steps 1-4: read kid/alg from JWT header → fetch JWKS →
                # match kid → build RSA public key. Runs in a thread because
                # get_signing_key_from_jwt() is synchronous (may do an HTTP fetch).
                signing_key = await asyncio.to_thread(
                    client.get_signing_key_from_jwt, token  # type: ignore[attr-defined]
                )
                # Steps 5-6: verify RS256 signature + validate iss, aud, exp, nbf.
                decode_kwargs: dict = {"algorithms": ["RS256"], "issuer": issuer}
                if audience:
                    decode_kwargs["audience"] = audience   # aud=MCP_SERVER_ID enforced
                else:
                    decode_kwargs["options"] = {"verify_aud": False}
                payload = _jwt.decode(token, signing_key.key, **decode_kwargs)

            except (
                _jwt.exceptions.InvalidSignatureError,
                _jwt.exceptions.InvalidAudienceError,
                _jwt.exceptions.InvalidIssuerError,
                _jwt.exceptions.ExpiredSignatureError,
            ) as hard_err:
                print(json.dumps({
                    "ts":    round(time.time(), 3),
                    "type":  "auth",
                    "valid": False,
                    "error": type(hard_err).__name__,
                    "path":  request.url.path,
                }, default=str))
                return _reject_401(type(hard_err).__name__)

            except Exception as soft_err:
                # Soft failure — JWKS unreachable; JWTVerifier is the upstream enforcer.
                print(json.dumps({
                    "ts":      round(time.time(), 3),
                    "type":    "auth",
                    "warning": (
                        f"JWKS soft-fail ({type(soft_err).__name__}: {soft_err})"
                        " — unverified decode"
                    ),
                    "path":    request.url.path,
                }, default=str))
                payload = _jwt.decode(token, options={"verify_signature": False})
        else:
            # PyJWT not installed — unverified decode; JWTVerifier is the enforcer.
            payload = _jwt.decode(token, options={"verify_signature": False})

        if payload:
            _set_claims(payload)
            print(json.dumps({
                "ts":            round(time.time(), 3),
                "type":          "auth",
                "sub":           payload.get("sub"),
                "roles":         payload.get("roles", ["agent"]),
                "jwks_verified": client is not None,
                "path":          request.url.path,
            }, default=str))

        return await call_next(request)


class BearerAuthMiddleware:
    """[DIRECTION 2 — INBOUND VERIFICATION] Full JWKS validation for stand-alone MCP servers.

    Pure ASGI middleware — does NOT extend BaseHTTPMiddleware.
    BaseHTTPMiddleware buffers the full response body before forwarding, which
    breaks SSE streaming (the server sends 'http.response.start' then 'body'
    chunks, but the buffer tries to read a complete body first → AssertionError).
    By implementing __call__(scope, receive, send) directly we pass through all
    ASGI messages without buffering, keeping SSE streams intact.

    Used by: mcp_middleware() → calc_server.py, weather_server.py, data_server.py
    Unlike BearerClaimsMiddleware, this is the SOLE gatekeeper (no FastMCP
    JWTVerifier upstream), so every failure is a hard 401 — no soft path.

    Standard JWKS verification flow:
        Step 1 — Extract JWT header: kid="hub-rsa-1", alg="RS256"
        Step 2 — Fetch JWKS: GET http://localhost:8090/.well-known/jwks.json
        Step 3 — Match key by kid
        Step 4 — Build RSA public key  (Steps 1-4: PyJWKClient, run in thread)
        Step 5 — Verify RS256 signature
        Step 6 — Validate: iss, aud (env: MCP_SERVER_ID), exp, nbf
                 (Steps 5-6: jwt.decode)

    Dev-mode shortcuts (no JWKS call needed):
        MCP_AUTH_ENABLED=false        → pass through as anonymous/admin
        No token + no MCP_SERVER_ID   → open dev mode; pass as dev/admin
        No token + MCP_SERVER_ID set  → 401 no_token
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        import jwt as _jwt  # PyJWT

        # _StarletteHeaders decodes byte-string header names/values from scope
        auth  = _StarletteHeaders(scope=scope).get("authorization", "")
        token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""

        # ── Dev-mode shortcuts — no JWKS call needed ──────────────────────────
        if not MCP_AUTH_ENABLED:
            _request_claims.set({"sub": "anonymous", "roles": ["admin"]})
            await self.app(scope, receive, send)
            return

        if not token:
            if not os.environ.get("MCP_SERVER_ID"):
                # No token + no MCP_SERVER_ID = open dev mode (local testing only).
                # WARNING: always set MCP_SERVER_ID in production.
                _request_claims.set({"sub": "dev", "roles": ["admin"]})
                await self.app(scope, receive, send)
                return
            await _reject_401("no_token")(scope, receive, send)
            return

        # ── JWKS verification ─────────────────────────────────────────────────
        issuer   = os.environ.get("MCP_JWT_ISSUER", "fab-mcp-hub")
        audience = os.environ.get("MCP_SERVER_ID", "")
        client   = _get_jwks_client()

        if client is None:
            await _reject_401("jwt_unavailable")(scope, receive, send)
            return

        try:
            # Steps 1-4: read kid/alg → fetch JWKS → match kid → build RSA key.
            # Runs in a thread because get_signing_key_from_jwt() is synchronous.
            signing_key = await asyncio.to_thread(
                client.get_signing_key_from_jwt, token  # type: ignore[attr-defined]
            )
            # Steps 5-6: verify RS256 signature + validate iss, aud, exp, nbf.
            decode_kwargs: dict = {"algorithms": ["RS256"], "issuer": issuer}
            if audience:
                decode_kwargs["audience"] = audience   # aud=MCP_SERVER_ID enforced
            else:
                decode_kwargs["options"] = {"verify_aud": False}
            payload = _jwt.decode(token, signing_key.key, **decode_kwargs)

        except _jwt.exceptions.ExpiredSignatureError:
            await _reject_401("token_expired")(scope, receive, send)
            return
        except (
            _jwt.exceptions.InvalidSignatureError,
            _jwt.exceptions.InvalidAudienceError,
            _jwt.exceptions.InvalidIssuerError,
        ):
            await _reject_401("token_invalid")(scope, receive, send)
            return
        except _jwt.exceptions.DecodeError:
            await _reject_401("token_malformed")(scope, receive, send)
            return
        except Exception as exc:  # noqa: BLE001 — covers PyJWKClientError, network errors
            await _reject_401(f"jwks_error:{type(exc).__name__}")(scope, receive, send)
            return

        _set_claims(payload)
        print(json.dumps({
            "ts":    round(time.time(), 3),
            "type":  "auth",
            "valid": True,
            "sub":   payload.get("sub"),
            "roles": payload.get("roles", ["agent"]),
            "path":  scope.get("path", ""),
        }, default=str))
        await self.app(scope, receive, send)


# ---------------------------------------------------------------------------
# Middleware factory helpers
# ---------------------------------------------------------------------------

def claims_middleware() -> list[Middleware]:
    """Return [BearerClaimsMiddleware] for servers that use FastMCP JWTVerifier.

    Used by: customer_server.py, pricing_server.py
    BearerClaimsMiddleware performs independent JWKS verification (defense-in-depth)
    in addition to populating the claims ContextVar for require_role().
    """
    return [Middleware(BearerClaimsMiddleware)]


def mcp_middleware() -> list[Middleware]:
    """Return [BearerAuthMiddleware] for stand-alone servers without JWTVerifier.

    Used by: calc_server.py, weather_server.py, data_server.py
    Returns an empty list when MCP_AUTH_ENABLED=false (open dev mode).
    """
    if not MCP_AUTH_ENABLED:
        return []
    return [Middleware(BearerAuthMiddleware)]
