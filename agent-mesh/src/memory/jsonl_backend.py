"""File-based JSONL conversation backend (the active default).

Persists one JSON object per line, one line per message, in a per-session file::

    data/conversations/{session_id}.jsonl
        {"role": "user",      "content": "...", "ts": "2026-06-29T..."}
        {"role": "assistant", "content": "...", "ts": "2026-06-29T..."}

Zero new infrastructure — uses ``Config.CONVERSATION_STORE_DIR`` (already in
config). Dependency-free (stdlib only).
"""
from __future__ import annotations

import json
import pathlib
import re
import datetime
from typing import List, Dict

from src.config import Config
from src.memory.base import ConversationBackend

# agent-mesh root: src/memory/jsonl_backend.py -> parents[2] == agent-mesh/
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]

# session_id is used as a filename — restrict to a safe charset to prevent path
# traversal or invalid filenames. Anything else is replaced with "_".
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


class JsonlBackend(ConversationBackend):
    """Stores conversation messages as append-only JSONL files, one per session."""

    def __init__(self, store_dir: str | None = None) -> None:
        configured = store_dir or Config.CONVERSATION_STORE_DIR
        base = pathlib.Path(configured)
        # Resolve relative paths against the agent-mesh root so the location is
        # stable regardless of which process/CWD invokes the store.
        self._dir = base if base.is_absolute() else (_PROJECT_ROOT / base)

    def _path(self, session_id: str) -> pathlib.Path:
        safe = _UNSAFE.sub("_", session_id or "default_session")
        self._dir.mkdir(parents=True, exist_ok=True)
        return self._dir / f"{safe}.jsonl"

    def load_messages(self, session_id: str) -> List[Dict]:
        path = self._path(session_id)
        if not path.exists():
            return []
        messages: List[Dict] = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # tolerate corrupt lines
                if isinstance(rec, dict) and rec.get("role") and "content" in rec:
                    messages.append(rec)
        return messages

    def append(self, session_id: str, role: str, content: str) -> None:
        path = self._path(session_id)
        record = {
            "role": role,
            "content": content,
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def clear(self, session_id: str) -> None:
        path = self._path(session_id)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
