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


def get_engine() -> Engine:
    """Build a SQLAlchemy engine using MCP service credentials (MYSQL_USER / MYSQL_PASSWORD).

    Service-layer contract:
    - Credentials come exclusively from environment variables (service identity).
    - The agent's JWT is NEVER used here — it is consumed at BearerAuthMiddleware.
    - Call audit_log() in your tool function BEFORE calling get_engine() to create
      an immutable audit trail linking the agent's verified identity to this DB access.

    Raises:
        RuntimeError: if MYSQL_USER or MYSQL_PASSWORD is not set in .env
    """
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

    engine = create_engine(url, echo=False, pool_pre_ping=True, pool_recycle=1800)

    # Log with agent context when called from within a request (for audit trail).
    agent_sub = "unknown"
    try:
        from mcp_server.auth import get_agent_context as _gac
        agent_sub = _gac().get("sub", "unknown")
    except ImportError:
        try:
            from auth import get_agent_context as _gac
            agent_sub = _gac().get("sub", "unknown")
        except ImportError:
            pass

    logger.info(
        "DB engine created | service_user=%s | host=%s:%d | db=%s | agent_sub=%s",
        user, host, port, database, agent_sub,
    )
    return engine
