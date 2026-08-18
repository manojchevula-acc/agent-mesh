"""
Structured observability for FAB MCP Hub.

Events are written to MySQL `hub_events` table (primary store) AND to an
in-memory ring buffer (fast fallback when MySQL is unavailable).
Accessible via GET /api/logs.

Event types
-----------
Hub-native (logged directly by hub_server.py):
  auth             — every Bearer-token check (valid/invalid, sub, roles, endpoint)
  request          — every HTTP request (method, path, status, latency_ms)
  routing          — each routing decision (method, server_ids, reason, intent)
  admin            — server CRUD, key rotation, credential operations
  error            — caught runtime errors

Agent-lifecycle (bridged from chat_server.py on_event → _hub_log_event):
  mcp_connecting   — agent starting to connect to an MCP server
  mcp_connected    — agent successfully connected; tool/prompt/resource counts
  mcp_capabilities — MCP server prompts and resources discovered (server_id, prompts[], resources[])
  mcp_prompt_used  — structured prompt matched and applied (prompt_name, prompt_args, message_count)
  error            — agent-side errors forwarded to hub observability

All agent-lifecycle events include session_id and sub (user identity) for correlation
with chat_traces records.
"""
from __future__ import annotations

import collections
import json
import os
import pathlib
import threading
import time
from typing import Any

try:
    from sqlalchemy import text as _sa_text
except ImportError:  # pragma: no cover
    _sa_text = None  # type: ignore[assignment]

MAX_EVENTS: int = 500

_buffer: collections.deque[dict] = collections.deque(maxlen=MAX_EVENTS)
_lock = threading.Lock()

# ---------------------------------------------------------------------------
# File-based log sink  (logs/hub.log in the project root, JSONL format)
# ---------------------------------------------------------------------------
_LOG_DIR = pathlib.Path(__file__).resolve().parent.parent / "logs"
_LOG_FILE = _LOG_DIR / "hub.log"
_log_fh: "Any | None" = None
_log_lock = threading.Lock()


def _open_log_file():
    global _log_fh
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        _log_fh = open(_LOG_FILE, "a", encoding="utf-8", buffering=1)  # line-buffered
    except Exception as exc:
        print(f"[observability] Cannot open log file {_LOG_FILE}: {exc}")


# Called once at module import time (not per-event) so the file descriptor is
# opened eagerly. Opening a file on every log_event() call would add syscall
# overhead at high event rates. If logs/ is unwritable, _open_log_file() prints
# a warning to stdout but continues — logging degrades gracefully to
# in-memory + stdout only.
_open_log_file()


def _write_log(entry: dict) -> None:
    with _log_lock:
        if _log_fh:
            try:
                _log_fh.write(json.dumps(entry, default=str) + "\n")
            except Exception:
                pass

_db_engine = None
# Once MySQL fails, _db_failed is permanently set to True for the process
# lifetime. _get_db_engine() returns None immediately on all subsequent calls,
# skipping the connection attempt entirely. This avoids a flood of slow
# MySQL-connect retries on every log_event() call when the DB is down.
# To recover: restart the hub process after MySQL becomes available.
_db_failed = False


def _get_db_engine():
    """Lazily initialise MySQL engine and create hub_events table."""
    global _db_engine, _db_failed
    if _db_failed:
        return None
    if _db_engine is not None:
        return _db_engine
    try:
        try:
            from db import get_engine           # hub_service/db.py on sys.path (hub_server.py context)
        except ImportError:
            from hub_service.db import get_engine   # project root on sys.path (chat_server.py context)
        eng = get_engine()
        with eng.begin() as conn:
            conn.execute(_sa_text("""
                CREATE TABLE IF NOT EXISTS hub_events (
                    id         BIGINT AUTO_INCREMENT PRIMARY KEY,
                    ts         DOUBLE       NOT NULL,
                    type       VARCHAR(64)  NOT NULL,
                    data       JSON,
                    created_at TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_he_type (type),
                    INDEX idx_he_ts   (ts)
                )
            """))
        _db_engine = eng
    except Exception as exc:
        print(f"[observability] MySQL unavailable, events in-memory only: {exc}")
        _db_failed = True
    return _db_engine


def log_event(event_type: str, **data: Any) -> None:
    """Append one structured event to the buffer, stdout, file, and MySQL."""
    entry: dict = {"ts": round(time.time(), 3), "type": event_type, **data}
    with _lock:
        _buffer.append(entry)
    line = json.dumps(entry, default=str)
    print(line)
    _write_log(entry)
    try:
        eng = _get_db_engine()
        if eng:
            with eng.begin() as conn:
                conn.execute(
                    _sa_text("INSERT INTO hub_events (ts, type, data) VALUES (:ts, :type, :data)"),
                    {"ts": entry["ts"], "type": event_type,
                     "data": json.dumps(entry, default=str)},
                )
    except Exception:
        # MySQL write failure is silenced: the event is still in the in-memory
        # deque and the JSONL file, so observability is not lost — just the DB
        # copy. This avoids cascading errors if MySQL becomes temporarily slow.
        pass


def get_events(n: int = 100, event_type: str | None = None) -> list[dict]:
    """Return the last *n* events, optionally filtered by type. Reads from MySQL if available."""
    try:
        eng = _get_db_engine()
        if eng:
            if event_type:
                sql = ("SELECT data FROM hub_events WHERE type = :type "
                       "ORDER BY ts DESC LIMIT :n")
                params: dict = {"type": event_type, "n": min(n, 5000)}
            else:
                sql = "SELECT data FROM hub_events ORDER BY ts DESC LIMIT :n"
                params = {"n": min(n, 5000)}
            with eng.connect() as conn:
                rows = conn.execute(_sa_text(sql), params).fetchall()
            events: list[dict] = []
            # The SQL query uses ORDER BY ts DESC so MySQL can use the idx_he_ts
            # index and stop at LIMIT without scanning the full table. reversed()
            # then re-sorts the fetched batch back to oldest-first so callers
            # receive events in chronological order (matching the in-memory path).
            for row in reversed(rows):
                try:
                    events.append(json.loads(row[0]))
                except Exception:
                    pass
            return events
    except Exception:
        pass
    # Fallback: in-memory buffer
    with _lock:
        events = list(_buffer)
    if event_type:
        events = [e for e in events if e.get("type") == event_type]
    return events[-n:]
