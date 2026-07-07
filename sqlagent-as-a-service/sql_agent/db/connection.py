"""Pooled connection using the SELECT-only credential.

The DSN user must hold SELECT-only grants on the five governed tables and nothing
else — enforced at the database, independently of the application-level validator
(Design Document §11.2 defence in depth; Technical Spec §13).
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from sql_agent.config import settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Returns a process-wide engine for the read-only credential.

    Works for PostgreSQL, MySQL and SQLite. SQLite uses its own default pool (it does
    not accept pool_size/max_overflow), so those are only applied to client/server DBs.
    Raises a clear error if DB_DSN is not configured rather than failing obscurely
    deep inside a tool call.
    """
    if not settings.db_dsn:
        raise RuntimeError(
            "DB_DSN is not configured. Set the SELECT-only credential in the "
            "environment (.env) before executing queries."
        )

    if settings.db_dsn.lower().startswith("sqlite"):
        # SQLite (file or :memory:) — let SQLAlchemy choose the appropriate pool.
        return create_engine(settings.db_dsn, future=True)

    return create_engine(
        settings.db_dsn,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        future=True,
    )
