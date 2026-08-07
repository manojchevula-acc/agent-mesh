"""Hub-side authentication helpers for the FAB MCP stack.

The hub now acts as the lightweight auth service for the demo environment:
- it can mint signed JWTs for agents and MCP servers;
- it exposes a JWKS document so MCP servers can validate those tokens without
  sharing the private key;
- it remains backward-compatible with the older static API-key flow.
"""

from __future__ import annotations

try:
    import pathlib as _pathlib
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(_pathlib.Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

import argparse
import base64
import os
import pathlib
import time
from typing import Any

try:
    import jwt  # PyJWT
    from jwt import PyJWKClient  # type: ignore
except ImportError:  # pragma: no cover - exercised when dependency missing
    jwt = None  # type: ignore[assignment]
    PyJWKClient = None  # type: ignore[assignment]

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

AUTH_ENABLED: bool = os.environ.get("AUTH_ENABLED", "true").lower() in ("1", "true", "yes")
AUTH_PROVIDER: str = os.environ.get("AUTH_PROVIDER", "local")

_API_KEY = os.environ.get("HUB_API_KEY", "")
_API_KEY_ROLES = [r.strip() for r in os.environ.get("HUB_API_KEY_ROLES", "agent").split(",") if r.strip()]
_JWT_SECRET = os.environ.get("JWT_SECRET", "")
_JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
_HUB_JWKS_URL = os.environ.get("HUB_JWKS_URL", "")
_HUB_ISSUER = os.environ.get("HUB_JWT_ISSUER", "fab-mcp-hub")
_HUB_KEY_ID = os.environ.get("HUB_JWT_KID", "hub-rsa-1")

_HUB_KEY_DIR = pathlib.Path(__file__).resolve().parent / ".keys"
_HUB_PRIVATE_KEY_PATH = pathlib.Path(os.environ.get("HUB_PRIVATE_KEY_PATH", str(_HUB_KEY_DIR / "private.pem")))
_HUB_PUBLIC_KEY_PATH = pathlib.Path(os.environ.get("HUB_PUBLIC_KEY_PATH", str(_HUB_KEY_DIR / "public.pem")))

_AZURE_TENANT = os.environ.get("AZURE_TENANT_ID", "")
_AZURE_AUDIENCE = os.environ.get("AZURE_CLIENT_ID", "")

_ANON_CLAIMS = {"sub": "anonymous", "roles": ["admin"]}
_DEV_CLAIMS = {"sub": "dev", "roles": ["admin"]}

_DEV_MODE_ACTIVE: bool = (
    AUTH_ENABLED
    and AUTH_PROVIDER == "local"
    and not _API_KEY
    and not _JWT_SECRET
)
if _DEV_MODE_ACTIVE:
    import warnings as _w
    _w.warn(
        "\n⚠  HUB SECURITY: AUTH_ENABLED=true but no HUB_API_KEY or JWT_SECRET is set "
        "— operating in open dev mode (all requests granted admin).\n"
        "   Set JWT_SECRET (and matching value in chat server) to enable real auth.",
        stacklevel=2,
    )


def _ensure_key_pair() -> None:
    if _HUB_PRIVATE_KEY_PATH.exists() and _HUB_PUBLIC_KEY_PATH.exists():
        return
    _HUB_PRIVATE_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _HUB_PRIVATE_KEY_PATH.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    _HUB_PUBLIC_KEY_PATH.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )


def _load_private_key() -> str:
    _ensure_key_pair()
    return _HUB_PRIVATE_KEY_PATH.read_text(encoding="utf-8")


def _load_public_key() -> str:
    _ensure_key_pair()
    return _HUB_PUBLIC_KEY_PATH.read_text(encoding="utf-8")


def get_jwks() -> dict[str, Any]:
    if jwt is None:
        return {"keys": []}
    _ensure_key_pair()
    public_key = serialization.load_pem_public_key(_load_public_key().encode("utf-8"))
    public_numbers = public_key.public_numbers()

    def _b64(value: int) -> str:
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    return {
        "keys": [
            {
                "kty": "RSA",
                "kid": _HUB_KEY_ID,
                "use": "sig",
                "alg": "RS256",
                "n": _b64(public_numbers.n),
                "e": _b64(public_numbers.e),
            }
        ]
    }


def get_jwks_url() -> str:
    if _HUB_JWKS_URL:
        return _HUB_JWKS_URL
    return os.environ.get("HUB_SERVER_URL", "http://localhost:8090") + "/.well-known/jwks.json"


def _jwt_alg(token: str) -> str:
    """Return the algorithm from a JWT header without verifying the signature."""
    if jwt is None or not token:
        return ""
    try:
        return jwt.get_unverified_header(token).get("alg", "")
    except Exception:
        return ""


def verify_token(token: str, expected_audience: str | None = None, expected_issuer: str | None = None) -> tuple[bool, dict]:
    """Validate a bearer token, supporting static keys, HS256 JWTs, and RS256 JWTs.

    Algorithm routing: reads the JWT header so RS256 hub tokens and HS256 chat
    tokens can both be accepted when both JWT_SECRET and the RSA key pair exist.
    """
    if not AUTH_ENABLED:
        return True, _ANON_CLAIMS
    if AUTH_PROVIDER == "azure":
        return _verify_azure(token)
    if token:
        if _API_KEY and token == _API_KEY:
            return True, {"sub": "api-key-user", "roles": _API_KEY_ROLES, "_source": "apikey"}
        alg = _jwt_alg(token)
        if alg == "RS256":
            return _verify_jwt_rs256(token, expected_audience=expected_audience, expected_issuer=expected_issuer)
        if _JWT_SECRET:
            return _verify_jwt_local(token, expected_audience=expected_audience, expected_issuer=expected_issuer)
        # Unknown / unsigned token — try RS256 as last resort
        result = _verify_jwt_rs256(token, expected_audience=expected_audience, expected_issuer=expected_issuer)
        if result[0]:
            return result
    if _DEV_MODE_ACTIVE:
        return True, _DEV_CLAIMS
    return False, {"_error": "token_invalid"}


def generate_token(
    sub: str = "agent",
    roles: list[str] | None = None,
    audience: str | None = None,
    server_id: str | None = None,
    expires_hours: int = 24,
    issuer: str | None = None,
) -> str:
    """Mint an RS256 JWT for hub-to-MCP authentication."""
    if jwt is None:
        raise RuntimeError("PyJWT is not installed. Run: pip install PyJWT")
    if roles is None:
        roles = ["agent"]
    if audience is None and server_id:
        audience = server_id
    if issuer is None:
        issuer = _HUB_ISSUER
    _ensure_key_pair()
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": sub,
        "roles": roles,
        "iat": now,
        "exp": now + expires_hours * 3600,
        "iss": issuer,
    }
    if audience:
        payload["aud"] = audience
    if server_id:
        payload["server_id"] = server_id
    return jwt.encode(payload, _load_private_key(), algorithm="RS256", headers={"kid": _HUB_KEY_ID})


def generate_server_token(
    server_id: str,
    sub: str = "agent",
    roles: list[str] | None = None,
    expires_hours: int = 1,
) -> str:
    """Mint a short-lived RS256 JWT scoped to a single MCP server.

    The audience is set to server_id so the target MCP server's JWTVerifier
    can enforce that tokens are not reused across servers.

    Args:
        server_id:     Hub server ID (e.g. 'fab-customer-server').
        sub:           Token subject — the agent's identity.
        roles:         Agent roles to embed in the token (default: ['agent']).
        expires_hours: Token lifetime; default 1 h (short-lived by design).
    """
    return generate_token(
        sub=sub,
        roles=roles,
        audience=server_id,
        server_id=server_id,
        expires_hours=expires_hours,
    )


def _verify_jwt_rs256(token: str, expected_audience: str | None = None, expected_issuer: str | None = None) -> tuple[bool, dict]:
    if jwt is None:
        return False, {"_error": "jwt_unavailable"}
    try:
        kwargs: dict[str, Any] = {"algorithms": ["RS256"]}
        if expected_issuer is None:
            expected_issuer = _HUB_ISSUER
        if expected_issuer:
            kwargs["issuer"] = expected_issuer
        if expected_audience:
            kwargs["audience"] = expected_audience
        else:
            kwargs["options"] = {"verify_aud": False}
        payload = jwt.decode(token, _load_public_key(), **kwargs)
        roles = payload.get("roles", ["agent"])
        if isinstance(roles, str):
            roles = roles.split()
        return True, {
            "sub": payload.get("sub", "unknown"),
            "roles": roles,
            "iss": payload.get("iss"),
            "aud": payload.get("aud"),
            "server_id": payload.get("server_id"),
            "_source": "jwt",
        }
    except Exception as exc:  # pragma: no cover - exercised at runtime
        err_type = type(exc).__name__
        if "Expired" in err_type:
            return False, {"_error": "token_expired"}
        if "Invalid" in err_type or "Decode" in err_type:
            return False, {"_error": "token_invalid"}
        return False, {"_error": "verify_error"}


def _verify_jwt_local(token: str, expected_audience: str | None = None, expected_issuer: str | None = None) -> tuple[bool, dict]:
    try:
        kwargs: dict[str, Any] = {"algorithms": [_JWT_ALGORITHM]}
        # Don't enforce a default issuer for shared-secret tokens — the secret
        # is the trust anchor. Chat tokens use iss="fab-chat"; hub tokens use
        # iss="fab-mcp-hub". Both are valid when signed with JWT_SECRET.
        if expected_issuer:
            kwargs["issuer"] = expected_issuer
        if expected_audience:
            kwargs["audience"] = expected_audience
        else:
            kwargs["options"] = {"verify_aud": False}
        payload = jwt.decode(token, _JWT_SECRET, **kwargs)
        roles = payload.get("roles", ["agent"])
        if isinstance(roles, str):
            roles = [roles]
        return True, {
            "sub": payload.get("sub", "unknown"),
            "roles": roles,
            "iss": payload.get("iss"),
            "aud": payload.get("aud"),
            "server_id": payload.get("server_id"),
            "_source": "jwt",
        }
    except Exception as exc:  # pragma: no cover - exercised at runtime
        err_type = type(exc).__name__
        if "Expired" in err_type:
            return False, {"_error": "token_expired"}
        if "Invalid" in err_type or "Decode" in err_type:
            return False, {"_error": "token_invalid"}
        return False, {"_error": "verify_error"}


def _verify_azure(token: str) -> tuple[bool, dict]:
    if not _AZURE_TENANT or not _AZURE_AUDIENCE:
        raise RuntimeError("Azure auth requires AZURE_TENANT_ID and AZURE_CLIENT_ID env vars.")
    try:
        from jwt import PyJWKClient
    except ImportError:
        raise RuntimeError("pip install 'PyJWT[cryptography]' to enable Azure auth")
    try:
        jwks_uri = f"https://login.microsoftonline.com/{_AZURE_TENANT}/discovery/v2.0/keys"
        signing_key = PyJWKClient(jwks_uri).get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=_AZURE_AUDIENCE,
            issuer=f"https://sts.windows.net/{_AZURE_TENANT}/",
        )
        roles = payload.get("roles") or payload.get("scp", "agent").split()
        if isinstance(roles, str):
            roles = [roles]
        return True, {"sub": payload.get("sub", "azure-user"), "roles": roles}
    except Exception:
        return False, {}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Mint a JWT for the FAB MCP Hub",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python hub_service/auth.py --sub agent --roles agent --hours 24\n"
            "  python hub_service/auth.py --sub admin --roles admin --hours 8\n"
        ),
    )
    parser.add_argument("--sub", default="agent", help="Token subject (default: agent)")
    parser.add_argument("--hours", type=int, default=24, help="Expiry hours (default: 24)")
    parser.add_argument(
        "--roles",
        default="agent",
        help="Comma-separated roles (default: agent). Available: admin, agent, readonly",
    )
    parser.add_argument("--audience", default="", help="Token audience (default: none)")
    parser.add_argument("--server-id", default="", help="Optional server identifier")
    args = parser.parse_args()

    role_list = [r.strip() for r in args.roles.split(",") if r.strip()]
    token = generate_token(
        sub=args.sub,
        roles=role_list,
        audience=args.audience or None,
        server_id=args.server_id or None,
        expires_hours=args.hours,
    )
    print(f"\nGenerated JWT (valid {args.hours}h, sub={args.sub!r}, roles={role_list}):")
    print(token)
    print("\nRequest header:")
    print(f"  Authorization: Bearer {token}")
