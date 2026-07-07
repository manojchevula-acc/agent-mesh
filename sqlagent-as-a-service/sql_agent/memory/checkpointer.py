"""Build the LangGraph checkpointer from config (architecture §2.2).

Three backends, chosen by CHECKPOINTER_BACKEND in .env:

  memory   -> InMemorySaver   (default; no infra; lost on restart)
  sqlite   -> SqliteSaver     (a single .db file; durable; no server needed)
  postgres -> PostgresSaver   (connection pool; durable; multi-worker safe)

SQLite is the recommended starting point — zero infrastructure, durable across
restarts, and the same AGENT_DB_DSN file is used for both the checkpointer and
the metadata tables (sessions/turns/feedback/examples).

Postgres note: we use a connection pool rather than from_conn_string() because
from_conn_string() is a context manager that opens AND closes one connection —
fine for a script, wrong for a long-lived web service.
"""

from __future__ import annotations

import sqlite3
from functools import lru_cache

from sql_agent.config import settings
from sql_agent.logging_config import get_logger

log = get_logger("memory")


def _sqlite_path(dsn: str) -> str:
    """Extract the file path from a SQLite DSN.
    'sqlite:///./agent_meta.db' -> './agent_meta.db'
    'sqlite:////abs/path.db'   -> '/abs/path.db'
    """
    # Remove the sqlite:/// prefix
    return dsn.split("sqlite:///", 1)[-1] or ":memory:"


def _libpq_dsn(dsn: str) -> str:
    """Strip SQLAlchemy '+driver' so the same AGENT_DB_DSN works for both
    SQLAlchemy (metadata tables) and psycopg (checkpointer).
    'postgresql+psycopg://u:p@h/db' -> 'postgresql://u:p@h/db'
    """
    scheme, sep, rest = dsn.partition("://")
    if sep and "+" in scheme:
        scheme = scheme.split("+", 1)[0]
    return f"{scheme}{sep}{rest}"


@lru_cache(maxsize=1)
def get_checkpointer():
    backend = settings.checkpointer_backend.lower()

    # --- SQLite (recommended: durable, no server) ----------------------------
    if backend == "sqlite":
        if not settings.agent_db_dsn:
            raise RuntimeError("CHECKPOINTER_BACKEND=sqlite requires AGENT_DB_DSN "
                               "(e.g. AGENT_DB_DSN=sqlite:///./agent_meta.db)")
        from langgraph.checkpoint.sqlite import SqliteSaver
        path = _sqlite_path(settings.agent_db_dsn)
        # check_same_thread=False is required for FastAPI (multiple threads share the conn)
        conn = sqlite3.connect(path, check_same_thread=False)
        cp = SqliteSaver(conn)
        cp.setup()  # idempotent: creates checkpointer tables if missing
        log.info("checkpointer: sqlite | file=%s", path)
        return cp

    # --- Postgres (multi-worker production) -----------------------------------
    if backend == "postgres":
        if not settings.agent_db_dsn:
            raise RuntimeError("CHECKPOINTER_BACKEND=postgres requires AGENT_DB_DSN")
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        pool = ConnectionPool(
            conninfo=_libpq_dsn(settings.agent_db_dsn),
            min_size=1,
            max_size=settings.agent_db_pool_max,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
            open=True,
        )
        cp = PostgresSaver(pool)
        cp.setup()
        log.info("checkpointer: postgres (pool max=%d)", settings.agent_db_pool_max)
        return cp

    # --- In-memory (default dev, lost on restart) ----------------------------
    from langgraph.checkpoint.memory import InMemorySaver
    log.info("checkpointer: in-memory (dev) — history lost on restart")
    return InMemorySaver()
