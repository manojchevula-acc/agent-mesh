"""
mcp_server/db.py
-----------------
Database engine factory for the FAB MCP server.

Service-layer authentication principle
---------------------------------------
The MCP server authenticates to MySQL using its own service credentials
(MYSQL_USER / MYSQL_PASSWORD from .env).  The agent's JWT is consumed at the
MCP middleware boundary and is NEVER forwarded to the database.

Security chain for every DB-backed tool call:

  Agent JWT ──► BearerAuthMiddleware (validates) ──► require_role() (RBAC)
      │                                                       │
      │   agent identity used for audit only                  │
      └─────────────────────────────────────────────────────►─┘
                                                              │
                                                         audit_log()
                                                              │
                                                         get_engine()  ← service creds
                                                              │
                                                         MySQL (MYSQL_USER / MYSQL_PASSWORD)
"""

import os
import logging
from urllib.parse import quote_plus

from sqlalchemy import create_engine, Engine
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load .env once when this module is imported
load_dotenv()

_engine: Engine | None = None


def get_engine() -> Engine:
    """Return (or create) the shared SQLAlchemy engine using MCP service credentials.

    The engine is created once and cached for the process lifetime.
    SQLAlchemy's connection pool handles concurrent requests safely.

    Service-layer contract:
    - Credentials come exclusively from environment variables (service identity).
    - The agent's JWT is NEVER used here — it is consumed at BearerAuthMiddleware.
    - Call audit_log() in your tool function BEFORE calling get_engine() to create
      an immutable audit trail linking the agent's verified identity to this DB access.

    Raises:
        RuntimeError: if MYSQL_USER or MYSQL_PASSWORD is not set in .env
    """
    global _engine
    if _engine is not None:
        return _engine

    host     = os.getenv("MYSQL_HOST", "127.0.0.1")
    port     = int(os.getenv("MYSQL_PORT", "3306"))
    user     = os.getenv("MYSQL_USER", "")
    password = os.getenv("MYSQL_PASSWORD", "")
    database = os.getenv("MYSQL_DATABASE", "fab_semantic")

    if not user:
        raise RuntimeError("MYSQL_USER is not set in .env")
    if not password:
        raise RuntimeError("MYSQL_PASSWORD is not set in .env")

    url = (
        f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{port}/{database}?charset=utf8mb4"
    )
    _engine = create_engine(url, echo=False, pool_pre_ping=True, pool_recycle=1800)
    logger.info("MCP DB engine → %s:%d / %s (user=%s)", host, port, database, user)
    return _engine
