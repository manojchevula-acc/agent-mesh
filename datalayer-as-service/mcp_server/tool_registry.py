"""
datalayer-as-service/mcp_server/tool_registry.py
--------------------------------------------------
SQLite-backed credential registry for external tools / services.

Architecture
------------
When an MCP tool calls an external service (credit bureau, FX rates, sanctions
screening), that service requires its own authentication — completely independent
from the agent's JWT (MCP_API_KEY) and the hub credentials (HUB_API_KEY).

The registry stores one row per external tool:

  tool_name        → matches the @mcp.tool() function name
  service_url      → base URL of the external service
  auth_type        → "bearer_jwt" | "api_key_header" | "basic"
  credential       → token/key the MCP server sends to the external service
  auth_header_name → HTTP header name (default: "Authorization")
  expires_at       → credential expiry timestamp (NULL = never expires)

Security model
--------------
  Agent JWT ──► MCP BearerAuthMiddleware              (MCP_API_KEY / MCP_JWT_SECRET)
                       │
                       ▼
              require_role() + audit_log()
                       │
                       ▼
              get_tool_credentials("credit_bureau_check")   ← THIS FILE
                       │ returns ToolCredential
                       ▼
              httpx.post(service_url, headers=creds.auth_headers())
                       │ uses credential field from registry
                       ▼
              External Service  ──► validates its OWN token
                                    independent from MCP_API_KEY

  ┌────────────────────────────────────────────────────────────────────┐
  │  The agent JWT is NEVER forwarded to the external service.        │
  │  Each external service uses its own credential (rotated separately)│
  └────────────────────────────────────────────────────────────────────┘

Production notes
----------------
- Credentials are stored in plain text in the SQLite file (dev/demo only).
  In production, replace `credential` with a reference to a secrets vault
  (Azure Key Vault, HashiCorp Vault, AWS Secrets Manager) and fetch at call time.
- Rotate credentials without restarting the MCP server:
    python datalayer-as-service/mcp_server/tool_registry.py --rotate <tool> <new_cred>

Database
--------
  datalayer-as-service/tool_credentials.db   (SQLite, auto-created)
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sqlite3
import time
from dataclasses import dataclass

_DB_PATH = pathlib.Path(__file__).resolve().parent.parent / "tool_credentials.db"


@dataclass
class ToolCredential:
    """Credential entry for one external tool/service."""
    tool_name:        str
    service_url:      str
    auth_type:        str
    credential:       str
    auth_header_name: str        = "Authorization"
    expires_at:       float | None = None

    def auth_headers(self) -> dict[str, str]:
        """Return HTTP headers dict to authenticate to the external service.

        These headers are for the MCP server's outbound request ONLY.
        They are never derived from or related to the agent's JWT.
        """
        if self.auth_type == "bearer_jwt":
            return {self.auth_header_name: f"Bearer {self.credential}"}
        if self.auth_type == "api_key_header":
            return {self.auth_header_name: self.credential}
        if self.auth_type == "basic":
            import base64
            return {self.auth_header_name:
                    "Basic " + base64.b64encode(self.credential.encode()).decode()}
        return {}


def _get_conn(db_path: pathlib.Path = _DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tool_credentials (
            tool_name        TEXT    PRIMARY KEY,
            service_url      TEXT    NOT NULL,
            auth_type        TEXT    NOT NULL DEFAULT 'bearer_jwt',
            credential       TEXT    NOT NULL,
            auth_header_name TEXT    NOT NULL DEFAULT 'Authorization',
            expires_at       REAL,
            created_at       REAL    NOT NULL,
            last_rotated_at  REAL,
            notes            TEXT
        )
    """)
    conn.commit()
    return conn


def get_tool_credentials(
    tool_name: str,
    db_path: pathlib.Path = _DB_PATH,
) -> ToolCredential:
    """Fetch auth credentials for an external tool/service.

    Call this inside an MCP tool function AFTER require_role() and audit_log(),
    to obtain the credential the MCP server uses for its outbound call.

    Args:
        tool_name: Matches the @mcp.tool() function name (e.g. "credit_bureau_check")

    Returns:
        ToolCredential with .auth_headers() ready for httpx/requests

    Raises:
        KeyError     — tool not registered; seed with: python tool_registry.py --seed
        RuntimeError — credential has expired; rotate with: python tool_registry.py --rotate
    """
    conn = _get_conn(db_path)
    row = conn.execute(
        "SELECT * FROM tool_credentials WHERE tool_name = ?", (tool_name,)
    ).fetchone()
    conn.close()

    if row is None:
        raise KeyError(
            f"No credentials for external tool {tool_name!r}.\n"
            f"  Seed defaults : python datalayer-as-service/mcp_server/tool_registry.py --seed\n"
            f"  Or register   : python datalayer-as-service/mcp_server/tool_registry.py "
            f"--register {tool_name} <url> <credential>"
        )

    expires_at = row["expires_at"]
    if expires_at and time.time() > expires_at:
        raise RuntimeError(
            f"Credential for {tool_name!r} expired at {expires_at:.0f}. "
            f"Rotate: python datalayer-as-service/mcp_server/tool_registry.py "
            f"--rotate {tool_name} <new_credential>"
        )

    return ToolCredential(
        tool_name=row["tool_name"],
        service_url=row["service_url"],
        auth_type=row["auth_type"],
        credential=row["credential"],
        auth_header_name=row["auth_header_name"],
        expires_at=expires_at,
    )


def register_tool_credential(
    tool_name: str,
    service_url: str,
    credential: str,
    auth_type: str = "bearer_jwt",
    auth_header_name: str = "Authorization",
    expires_hours: int | None = None,
    notes: str = "",
    db_path: pathlib.Path = _DB_PATH,
) -> None:
    """Register or update credentials for an external tool/service."""
    now = time.time()
    expires_at = now + expires_hours * 3600 if expires_hours else None
    conn = _get_conn(db_path)
    conn.execute(
        """
        INSERT INTO tool_credentials
            (tool_name, service_url, auth_type, credential, auth_header_name,
             expires_at, created_at, last_rotated_at, notes)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(tool_name) DO UPDATE SET
            service_url      = excluded.service_url,
            auth_type        = excluded.auth_type,
            credential       = excluded.credential,
            auth_header_name = excluded.auth_header_name,
            expires_at       = excluded.expires_at,
            last_rotated_at  = ?,
            notes            = excluded.notes
        """,
        (tool_name, service_url, auth_type, credential, auth_header_name,
         expires_at, now, now, notes,
         now),
    )
    conn.commit()
    conn.close()


def rotate_tool_credential(
    tool_name: str,
    new_credential: str,
    expires_hours: int | None = None,
    db_path: pathlib.Path = _DB_PATH,
) -> None:
    """Rotate (replace) the stored credential for a registered tool.

    Safe to call at runtime — no server restart required.
    """
    now = time.time()
    expires_at = now + expires_hours * 3600 if expires_hours else None
    conn = _get_conn(db_path)
    result = conn.execute(
        "UPDATE tool_credentials SET credential=?, expires_at=?, last_rotated_at=? "
        "WHERE tool_name=?",
        (new_credential, expires_at, now, tool_name),
    )
    conn.commit()
    conn.close()
    if result.rowcount == 0:
        raise KeyError(f"Tool {tool_name!r} not found in registry. Register it first.")


def list_tool_credentials(db_path: pathlib.Path = _DB_PATH) -> list[dict]:
    """List all registered tools with credentials redacted."""
    conn = _get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM tool_credentials ORDER BY tool_name"
    ).fetchall()
    conn.close()
    result = []
    for row in rows:
        d = dict(row)
        cred = d.get("credential", "")
        d["credential"] = (cred[:6] + "***") if len(cred) > 6 else "***"
        d["status"] = (
            "expired" if (d["expires_at"] and time.time() > d["expires_at"]) else "active"
        )
        result.append(d)
    return result


# ── Default dev credentials ────────────────────────────────────────────────
# These match the defaults in external_service.py.
# Override via environment variables before seeding for production.

_DEV_TOOLS: list[dict] = [
    {
        "tool_name":        "credit_bureau_check",
        "service_url":      "http://localhost:8010/check",
        "auth_type":        "bearer_jwt",
        "credential":       os.environ.get("CREDIT_BUREAU_API_KEY", "credit-bureau-dev-token"),
        "auth_header_name": "Authorization",
        "notes":            "External credit bureau — Bearer JWT independent from MCP_API_KEY",
    },
    {
        "tool_name":        "fx_rate_lookup",
        "service_url":      "http://localhost:8010/fx",
        "auth_type":        "api_key_header",
        "credential":       os.environ.get("FX_RATE_API_KEY", "fx-rate-dev-key"),
        "auth_header_name": "X-API-Key",
        "notes":            "FX rate provider — X-API-Key header (NOT Bearer), different auth pattern",
    },
    {
        "tool_name":        "sanctions_screen",
        "service_url":      "http://localhost:8010/sanctions",
        "auth_type":        "bearer_jwt",
        "credential":       os.environ.get("SANCTIONS_API_KEY", "sanctions-dev-token"),
        "auth_header_name": "Authorization",
        "notes":            "Compliance sanctions check — admin-only MCP tool, separate service token",
    },
]


def seed_dev_credentials(db_path: pathlib.Path = _DB_PATH) -> None:
    """Seed the registry with development credentials. Safe to re-run."""
    for t in _DEV_TOOLS:
        register_tool_credential(db_path=db_path, **t)
    print(f"Seeded {len(_DEV_TOOLS)} tool credentials → {db_path}")
    print(f"\n  {'TOOL':30s} {'AUTH TYPE':20s} {'SERVICE URL'}")
    print(f"  {'-'*80}")
    for t in list_tool_credentials(db_path):
        print(f"  {t['tool_name']:30s} {t['auth_type']:20s} {t['service_url']}")
    print(
        "\nNote: credentials stored in plain text (dev mode). "
        "Use a secrets vault in production."
    )


# ── CLI ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Tool Credential Registry — manage per-tool external service auth",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python tool_registry.py --seed\n"
            "  python tool_registry.py --list\n"
            "  python tool_registry.py --register credit_bureau_check "
            "http://svc/check my-prod-token\n"
            "  python tool_registry.py --rotate credit_bureau_check new-rotated-token\n"
            "  python tool_registry.py --rotate credit_bureau_check new-token "
            "--expires-hours 720\n"
        ),
    )
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--seed",     action="store_true",  help="Seed default dev credentials")
    grp.add_argument("--list",     action="store_true",  help="List all registered tools")
    grp.add_argument("--register", nargs=3,
                     metavar=("TOOL_NAME", "SERVICE_URL", "CREDENTIAL"),
                     help="Register or update a tool credential")
    grp.add_argument("--rotate",   nargs=2,
                     metavar=("TOOL_NAME", "NEW_CREDENTIAL"),
                     help="Rotate credential for a registered tool")
    grp.add_argument("--get",      metavar="TOOL_NAME",
                     help="Show credential info for one tool (credential redacted)")

    parser.add_argument("--auth-type",     default="bearer_jwt",
                        choices=["bearer_jwt", "api_key_header", "basic"],
                        help="Auth type for --register (default: bearer_jwt)")
    parser.add_argument("--header",        default="Authorization",
                        help="Header name for --register (default: Authorization)")
    parser.add_argument("--expires-hours", type=int, default=None,
                        help="Expiry in hours (default: never)")
    parser.add_argument("--notes",         default="",
                        help="Notes for --register")
    parser.add_argument("--db",            default=str(_DB_PATH),
                        help=f"SQLite DB path (default: {_DB_PATH})")

    args = parser.parse_args()
    db_path = pathlib.Path(args.db)

    if args.seed:
        seed_dev_credentials(db_path)

    elif args.list:
        rows = list_tool_credentials(db_path)
        if not rows:
            print("No tool credentials registered. Run: python tool_registry.py --seed")
        else:
            print(f"\n  {'TOOL':30s} {'AUTH TYPE':20s} {'STATUS':8s} SERVICE URL")
            print(f"  {'-'*90}")
            for r in rows:
                print(f"  {r['tool_name']:30s} {r['auth_type']:20s} "
                      f"{r['status']:8s} {r['service_url']}")

    elif args.register:
        tool_name, service_url, credential = args.register
        register_tool_credential(
            tool_name=tool_name,
            service_url=service_url,
            credential=credential,
            auth_type=args.auth_type,
            auth_header_name=args.header,
            expires_hours=args.expires_hours,
            notes=args.notes,
            db_path=db_path,
        )
        print(f"Registered: {tool_name}  →  {service_url}")

    elif args.rotate:
        tool_name, new_credential = args.rotate
        rotate_tool_credential(tool_name, new_credential, args.expires_hours, db_path)
        print(f"Rotated credential for: {tool_name}")

    elif args.get:
        rows = list_tool_credentials(db_path)
        match = next((r for r in rows if r["tool_name"] == args.get), None)
        if match:
            print(json.dumps(match, indent=2, default=str))
        else:
            print(f"Tool {args.get!r} not found in registry")
