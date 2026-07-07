"""Metadata database for sessions, turns, feedback, and curated examples.

Separate from sql_agent/db (the governed, read-only business DB). This one is
read-write but only touches the agent's own bookkeeping tables — never business data.

When AGENT_DB_DSN is unset (dev), get_engine() returns None and every writer here is a
no-op, so the agent runs with the in-memory checkpointer and no metadata DB at all.
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    func,
)

from sql_agent.config import settings

metadata = MetaData()

sessions = Table(
    "sessions", metadata,
    Column("session_id", String(64), primary_key=True),
    Column("user_id", String(64), nullable=False, index=True),
    Column("title", String(256)),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column("last_active_at", DateTime(timezone=True), server_default=func.now()),
)

# Readable history (architecture §5.1/§5.2): one plain row per turn, for the UI + audit.
# This is SEPARATE from the checkpointer (the agent's working memory). It never holds
# full result rows or sensitive columns — only the question, answer, tier/tool, and
# (analytical) the validated SQL.
turns = Table(
    "turns", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", String(64), index=True),
    Column("user_id", String(64), index=True),
    Column("turn_ref", String(64)),            # = correlation_id; links feedback to this turn
    Column("question", Text, nullable=False),
    Column("answer", Text),
    Column("tier", String(32)),
    Column("tool", String(64)),
    Column("validated_sql", Text),             # analytical tier only
    Column("rows_returned", Integer),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)

feedback = Table(
    "feedback", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", String(64), index=True),
    Column("turn_ref", String(64)),            # correlation id / message id of the rated turn
    Column("user_id", String(64), index=True),
    Column("signal_type", String(32)),         # explicit_rating | explicit_correction | implicit
    Column("polarity", String(16)),            # positive | negative | neutral
    Column("stage", String(32)),               # routing | generation | filter_selection | ...
    Column("correction_text", Text),
    Column("status", String(16), default="new", index=True),  # new | reviewed | promoted
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)

examples = Table(
    "examples", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("question", Text, nullable=False),
    Column("validated_sql", Text),             # analytical examples only
    Column("tier", String(32)),
    Column("tags", String(256)),
    Column("status", String(16), default="candidate", index=True),  # candidate | approved | retired
    Column("approved_by", String(64)),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)

_engine = None


def get_engine():
    """Lazy singleton. Returns None when AGENT_DB_DSN is unset (dev / no metadata DB)."""
    global _engine
    if not settings.agent_db_dsn:
        return None
    if _engine is None:
        _engine = create_engine(settings.agent_db_dsn, pool_pre_ping=True)
    return _engine


def init_tables() -> None:
    """Create the metadata tables if a metadata DB is configured."""
    engine = get_engine()
    if engine is not None:
        metadata.create_all(engine)
