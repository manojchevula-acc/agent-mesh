"""
Session Logger — structured file logging for the Retail Banking Handoff POC.

Creates one log file per session: logs/<session_id>.log
Opened on session_created, closed (flushed) on session_done or session_deleted.
Each line is a JSON record.
"""

import json
import logging
import pathlib
from datetime import datetime, timezone


_LOG_DIR = pathlib.Path(__file__).resolve().parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)

_FMT = logging.Formatter("%(message)s")


def _make_handler(path: pathlib.Path) -> logging.FileHandler:
    h = logging.FileHandler(str(path), encoding="utf-8")
    h.setFormatter(_FMT)
    return h


class SessionLogger:
    """Writes structured JSON log lines to logs/<session_id>.log."""

    def __init__(self) -> None:
        self._session_loggers: dict[str, logging.Logger] = {}
        self._session_handlers: dict[str, logging.FileHandler] = {}

    # ── Per-session log ───────────────────────────────────────────────────────

    def _open_session_log(self, session_id: str) -> None:
        if session_id in self._session_loggers:
            return
        logger = logging.getLogger(f"rbh.session.{session_id}")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        path = _LOG_DIR / f"{session_id}.log"
        handler = _make_handler(path)
        logger.addHandler(handler)
        self._session_loggers[session_id] = logger
        self._session_handlers[session_id] = handler

    def _close_session_log(self, session_id: str) -> None:
        handler = self._session_handlers.pop(session_id, None)
        logger = self._session_loggers.pop(session_id, None)
        if logger and handler:
            logger.removeHandler(handler)
            handler.close()

    # ── Core write ────────────────────────────────────────────────────────────

    def _write(self, session_id: str, record: dict) -> None:
        sess_logger = self._session_loggers.get(session_id)
        if sess_logger:
            sess_logger.info(json.dumps(record, ensure_ascii=False))

    # ── Public API ────────────────────────────────────────────────────────────

    def session_created(self, session_id: str, agents: list[str]) -> None:
        self._open_session_log(session_id)
        self._write(session_id, {
            "ts": _ts(), "session_id": session_id,
            "event": "SESSION_CREATED", "agents": agents,
        })

    def user_message(self, session_id: str, text: str) -> None:
        self._write(session_id, {
            "ts": _ts(), "session_id": session_id,
            "event": "USER_MESSAGE", "text": text,
        })

    def log_sse(self, session_id: str, event: dict) -> None:
        etype = event.get("type", "unknown")

        if etype == "agent_active":
            self._write(session_id, {
                "ts": _ts(), "session_id": session_id,
                "event": "AGENT_ACTIVE", "agent": event.get("agent"),
            })

        elif etype == "handoff":
            self._write(session_id, {
                "ts": _ts(), "session_id": session_id,
                "event": "HANDOFF",
                "from": event.get("from"), "to": event.get("to"),
            })

        elif etype == "tool_call":
            self._write(session_id, {
                "ts": _ts(), "session_id": session_id,
                "event": "TOOL_CALL",
                "agent": event.get("agent"),
                "tool": event.get("tool"),
                "args": event.get("args", {}),
            })

        elif etype == "token":
            self._write(session_id, {
                "ts": _ts(), "session_id": session_id,
                "event": "AGENT_RESPONSE",
                "agent": event.get("agent"),
                "text": event.get("text", ""),
            })

        elif etype == "reasoning":
            self._write(session_id, {
                "ts": _ts(), "session_id": session_id,
                "event": "REASONING",
                "agent": event.get("agent"),
                "called_because": event.get("called_because", ""),
                "context": event.get("context", ""),
                "action": event.get("action", ""),
                "decision": event.get("decision", ""),
            })

        elif etype == "needs_approval":
            self._write(session_id, {
                "ts": _ts(), "session_id": session_id,
                "event": "APPROVAL_REQUESTED",
                "tool": event.get("tool"),
                "gate": event.get("gate"),
                "role": event.get("role"),
                "args": event.get("args", {}),
            })

        elif etype == "needs_input":
            self._write(session_id, {
                "ts": _ts(), "session_id": session_id,
                "event": "AWAITING_USER_INPUT",
                "agent": event.get("agent"),
            })

        elif etype == "error":
            self._write(session_id, {
                "ts": _ts(), "session_id": session_id,
                "event": "ERROR", "message": event.get("message", ""),
            })

        elif etype == "session_done":
            self._write(session_id, {
                "ts": _ts(), "session_id": session_id,
                "event": "SESSION_DONE",
            })
            # Keep file open — session_deleted() will close it after logging that event too

    def approval_decision(self, session_id: str, request_id: str, approved: bool) -> None:
        self._write(session_id, {
            "ts": _ts(), "session_id": session_id,
            "event": "APPROVAL_DECISION",
            "request_id": request_id, "approved": approved,
        })

    def session_deleted(self, session_id: str) -> None:
        self._write(session_id, {
            "ts": _ts(), "session_id": session_id,
            "event": "SESSION_DELETED",
        })
        self._close_session_log(session_id)


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
