"""
hub_service/db.py
-----------------
MySQL connection factory for the hub server.

Uses the same fab_semantic database as the FAB MCP servers.
Credentials are read from datalayer-as-service/.env so there is one place
to configure the database regardless of which service is starting.

Connection is created once and cached in a module-level singleton.

Why credentials come from datalayer-as-service/.env (not root .env):
    The FAB MCP servers (customer_server, pricing_server, etc.) each load
    datalayer-as-service/.env for their MySQL connection. The hub uses the
    same database (fab_semantic) to store server registry and event logs, so
    it reuses that same credential file to avoid duplication and keep MySQL
    config in one place.
"""

import logging
import os
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine

logger = logging.getLogger(__name__)

# Resolve .env relative to this file: hub_service/ → project root → datalayer-as-service/
# load_dotenv() fires at import time (module-level), before get_engine() is first called.
# It uses override=False (the default), meaning os.environ values already set by the
# shell or root .env take precedence over what is in datalayer-as-service/.env.
_ENV_FILE = Path(__file__).parent.parent / "datalayer-as-service" / ".env"
load_dotenv(_ENV_FILE)

_engine: Engine | None = None


def get_engine() -> Engine:
    """Return (or create) the shared SQLAlchemy engine for fab_semantic.

    Fails fast with a clear RuntimeError when credentials are missing so
    startup logs show the problem immediately rather than failing later
    on the first SQL query with a cryptic connection-refused error.
    """
    global _engine
    if _engine is not None:
        return _engine

    host     = os.getenv("MYSQL_HOST",     "127.0.0.1")
    port     = int(os.getenv("MYSQL_PORT", "3306"))
    user     = os.getenv("MYSQL_USER",     "")
    password = os.getenv("MYSQL_PASSWORD", "")
    database = os.getenv("MYSQL_DATABASE", "fab_semantic")

    if not user:
        raise RuntimeError("MYSQL_USER not set — check datalayer-as-service/.env")
    if not password:
        raise RuntimeError("MYSQL_PASSWORD not set — check datalayer-as-service/.env")

    url = (
        f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{port}/{database}?charset=utf8mb4"
    )
    _engine = create_engine(
        url,
        echo=False,
        pool_pre_ping=True,   # send a lightweight SELECT 1 before reusing an idle connection
                              # to detect stale connections before they cause query failures
        pool_recycle=1800,    # recycle connections every 30 minutes; MySQL's default
                              # wait_timeout is 8 hours, but many managed DBs (RDS,
                              # Cloud SQL) use lower values. 30 min keeps connections
                              # well inside any reasonable timeout window.
    )
    logger.info("Hub DB engine → %s:%d / %s", host, port, database)
    return _engine
