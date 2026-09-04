"""Thread-scoped conversation store (architecture §2.2) — the MAF replacement for the
LangGraph checkpointer.

Three backends, chosen by CHECKPOINTER_BACKEND in .env (the key is deliberately
unchanged so existing deployments keep working):

  memory   -> in-process dict   (default; no infra; lost on restart)
  sqlite   -> one table in the AGENT_DB_DSN file (durable; no server needed)
  postgres -> one table via a psycopg pool (durable; multi-worker safe)

The unit of storage is the whole AgentState for a thread_id, so kg_context, intent and
resolved_entities survive across turns exactly as they did when the checkpointer
persisted the full state channel. Merge semantics live in agent/state.merge_state.

Postgres note: we use a connection pool rather than a one-shot connection because the
service is long-lived — same reasoning as the original checkpointer.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from functools import lru_cache

from agent_framework import Message

from sql_agent.config import settings
from sql_agent.logging_config import get_logger

log = get_logger("memory")

_DDL = """
CREATE TABLE IF NOT EXISTS agent_threads (
    thread_id TEXT PRIMARY KEY,
    state     TEXT NOT NULL,
    updated   DOUBLE PRECISION NOT NULL
)
"""


# --- serialisation ------------------------------------------------------------
# Message serialises with to_dict()/from_dict() (NOT pydantic model_dump); auth_scopes
# is a set. Round-trip verified in docs/maf/reference_spike.py.

def _dump(state: dict) -> str:
    out = dict(state)
    out["messages"] = [m.to_dict() for m in state.get("messages", [])]
    if isinstance(out.get("auth_scopes"), (set, frozenset)):
        out["auth_scopes"] = sorted(out["auth_scopes"])
    return json.dumps(out, default=str)


def _load(blob: str) -> dict:
    state = json.loads(blob)
    state["messages"] = [Message.from_dict(m) for m in state.get("messages", [])]
    if isinstance(state.get("auth_scopes"), list):
        state["auth_scopes"] = set(state["auth_scopes"])
    return state


class ConversationStore:
    def load(self, thread_id: str) -> dict | None: raise NotImplementedError
    def save(self, thread_id: str, state: dict) -> None: raise NotImplementedError


class InMemoryStore(ConversationStore):
    def __init__(self) -> None:
        self._d: dict[str, dict] = {}
        self._lock = threading.Lock()

    def load(self, thread_id):
        with self._lock:
            return self._d.get(thread_id)

    def save(self, thread_id, state):
        with self._lock:
            self._d[thread_id] = state


class SqliteStore(ConversationStore):
    def __init__(self, path: str) -> None:
        # check_same_thread=False is required for FastAPI (multiple threads share it)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(_DDL.replace("DOUBLE PRECISION", "REAL"))
            self._conn.commit()

    def load(self, thread_id):
        with self._lock:
            row = self._conn.execute(
                "SELECT state FROM agent_threads WHERE thread_id = ?",
                (thread_id,)).fetchone()
        return _load(row[0]) if row else None

    def save(self, thread_id, state):
        import time
        with self._lock:
            self._conn.execute(
                "INSERT INTO agent_threads (thread_id, state, updated) VALUES (?,?,?) "
                "ON CONFLICT(thread_id) DO UPDATE SET state=excluded.state, "
                "updated=excluded.updated",
                (thread_id, _dump(state), time.time()))
            self._conn.commit()


class PostgresStore(ConversationStore):
    def __init__(self, dsn: str, pool_max: int) -> None:
        from psycopg_pool import ConnectionPool
        self._pool = ConnectionPool(conninfo=dsn, min_size=1, max_size=pool_max,
                                    kwargs={"autocommit": True}, open=True)
        with self._pool.connection() as c:
            c.execute(_DDL)

    def load(self, thread_id):
        with self._pool.connection() as c:
            row = c.execute("SELECT state FROM agent_threads WHERE thread_id = %s",
                            (thread_id,)).fetchone()
        return _load(row[0]) if row else None

    def save(self, thread_id, state):
        import time
        with self._pool.connection() as c:
            c.execute(
                "INSERT INTO agent_threads (thread_id, state, updated) VALUES "
                "(%s,%s,%s) ON CONFLICT (thread_id) DO UPDATE SET "
                "state = EXCLUDED.state, updated = EXCLUDED.updated",
                (thread_id, _dump(state), time.time()))


# --- DSN helpers (moved verbatim from checkpointer.py) ------------------------

def _sqlite_path(dsn: str) -> str:
    return dsn.split("sqlite:///", 1)[-1] or ":memory:"


def _libpq_dsn(dsn: str) -> str:
    scheme, sep, rest = dsn.partition("://")
    if sep and "+" in scheme:
        scheme = scheme.split("+", 1)[0]
    return f"{scheme}{sep}{rest}"


@lru_cache(maxsize=1)
def get_conversation_store() -> ConversationStore:
    backend = settings.checkpointer_backend.lower()

    if backend == "sqlite":
        if not settings.agent_db_dsn:
            raise RuntimeError("CHECKPOINTER_BACKEND=sqlite requires AGENT_DB_DSN "
                               "(e.g. AGENT_DB_DSN=sqlite:///./agent_meta.db)")
        path = _sqlite_path(settings.agent_db_dsn)
        log.info("conversation store: sqlite | file=%s", path)
        return SqliteStore(path)

    if backend == "postgres":
        if not settings.agent_db_dsn:
            raise RuntimeError("CHECKPOINTER_BACKEND=postgres requires AGENT_DB_DSN")
        log.info("conversation store: postgres (pool max=%d)", settings.agent_db_pool_max)
        return PostgresStore(_libpq_dsn(settings.agent_db_dsn),
                             settings.agent_db_pool_max)

    log.info("conversation store: in-memory (dev) — history lost on restart")
    return InMemoryStore()


# Alias so callers written against the old name keep working during the cutover.
get_checkpointer = get_conversation_store
