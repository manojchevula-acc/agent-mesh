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

    def bind_session(self, session_id: str, user_name: str) -> None:
        """Record the owner of a session. No-op if already bound or backend lacks support."""
        if hasattr(self._backend, "write_owner"):
            self._backend.write_owner(session_id, user_name)

    def list_sessions(self, owner: str) -> List[Dict]:
        """List sessions owned by ``owner``, newest first. Empty list if the backend
        doesn't support listing (e.g. Redis, not yet implemented)."""
        if hasattr(self._backend, "list_sessions"):
            return self._backend.list_sessions(owner)
        return []

    def check_owner(self, session_id: str, requesting_user: str) -> bool:
        """Return True if requesting_user owns the session (or no ownership record exists).

        Backward-compatible: sessions created before ownership tracking was introduced
        have no .owner file and are allowed through to avoid breaking existing clients.
        """
        if not hasattr(self._backend, "read_owner"):
            return True
        owner = self._backend.read_owner(session_id)
        if owner is None:
            return True  # legacy session — allow for backward compatibility
        return owner == requesting_user

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

    @staticmethod
    def strip_history_echo(answer: str, original_query: str | None = None) -> str:
        """Remove any accidentally echoed [Conversation so far] block from an answer.

        When the LLM echoes the injected history prefix back in its response,
        this strips everything up to and including the [Current question] marker
        (and the echoed user query, if present) so only the actual answer is returned.
        """
        if "[Conversation so far]" not in answer:
            return answer
        marker = "[Current question]"
        idx = answer.find(marker)
        if idx == -1:
            # Partial echo without the closing marker — strip from the block start
            block_start = answer.find("[Conversation so far]")
            return answer[:block_start].strip() or answer
        after_marker = answer[idx + len(marker):].lstrip("\n ")
        # Skip an echoed copy of the user's own question if present
        if original_query and after_marker.startswith(original_query.strip()):
            after_marker = after_marker[len(original_query.strip()):].lstrip("\n ")
        return after_marker.strip() if after_marker.strip() else answer
