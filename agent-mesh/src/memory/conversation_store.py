"""Conversation store facade — the single entry point the mesh uses for memory.

Selects a :class:`~src.memory.base.ConversationBackend` from
``Config.CONVERSATION_BACKEND`` (``"jsonl"`` active default; ``"redis"`` future)
and exposes the higher-level operations the orchestrator/workflow need:

  - ``load(session_id, max_turns)``  — recent history capped to N turns (for the prompt)
  - ``append_turn(session_id, q, a)`` — persist one user→assistant exchange
  - ``load_messages(session_id)``    — full history (for the API restore endpoint)
  - ``format_history_block(messages)`` — render history as a prompt-injectable block
"""
from __future__ import annotations

from typing import List, Dict

from src.config import Config
from src.memory.base import ConversationBackend


def _build_backend() -> ConversationBackend:
    backend = (Config.CONVERSATION_BACKEND or "jsonl").strip().lower()
    if backend == "redis":
        from src.memory.redis_backend import RedisBackend
        return RedisBackend()
    # default / "jsonl"
    from src.memory.jsonl_backend import JsonlBackend
    return JsonlBackend()


def get_conversation_store() -> "ConversationStore":
    """Factory returning a store bound to the configured backend."""
    return ConversationStore()


class ConversationStore:
    """High-level conversation memory API, backend-agnostic."""

    def __init__(self, backend: ConversationBackend | None = None) -> None:
        self._backend = backend or _build_backend()

    def load(self, session_id: str, max_turns: int) -> List[Dict]:
        """Return the last ``max_turns`` exchanges as role/content message dicts.

        A "turn" is one user message + one assistant message, so the cap is
        ``2 * max_turns`` messages from the tail of history.
        """
        messages = self._backend.load_messages(session_id)
        if max_turns and max_turns > 0:
            messages = messages[-(2 * max_turns):]
        return messages

    def load_messages(self, session_id: str) -> List[Dict]:
        """Full history for ``session_id`` (used by the UI restore endpoint)."""
        return self._backend.load_messages(session_id)

    def append_turn(self, session_id: str, user_query: str, assistant_answer: str) -> None:
        """Persist one user→assistant exchange."""
        self._backend.append(session_id, "user", user_query)
        self._backend.append(session_id, "assistant", assistant_answer)

    def clear(self, session_id: str) -> None:
        self._backend.clear(session_id)

    @staticmethod
    def format_history_block(messages: List[Dict]) -> str:
        """Render prior messages as a delimited block to prepend to a query.

        Returns ``""`` when there is no history so callers can pass the raw query
        through unchanged.
        """
        if not messages:
            return ""
        lines = ["[Conversation so far]"]
        for m in messages:
            role = m.get("role", "")
            content = (m.get("content") or "").strip()
            if not content:
                continue
            label = "User" if role == "user" else "Assistant"
            lines.append(f"{label}: {content}")
        lines.append("")  # blank separator line
        lines.append("[Current question]")
        lines.append("")
        return "\n".join(lines)
