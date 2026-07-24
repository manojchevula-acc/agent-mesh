"""Conversation storage backend interface.

The mesh persists multi-turn conversation history keyed by ``session_id``. The
storage mechanism is abstracted behind :class:`ConversationBackend` so the active
file-based JSONL backend can be swapped for Redis (or any other store) by flipping
``Config.CONVERSATION_BACKEND`` — without touching the orchestrator or workflow.

Each message is a plain dict in MAF-compatible role/content form::

    {"role": "user" | "assistant", "content": str, "ts": "<ISO-8601>"}
"""
from __future__ import annotations

import abc
from typing import List, Dict


class ConversationBackend(abc.ABC):
    """Abstract persistence backend for conversation messages.

    Implementations must be safe to call across separate HTTP requests (the store
    is the only thing carrying state between otherwise-stateless mesh requests).
    """

    @abc.abstractmethod
    def load_messages(self, session_id: str) -> List[Dict]:
        """Return all stored messages for ``session_id`` in chronological order.

        Returns an empty list when the session has no history. Implementations
        should tolerate (skip) any malformed/corrupt records rather than raise.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def append(self, session_id: str, role: str, content: str, extra: Dict | None = None) -> None:
        """Append a single message (``role``/``content``) to ``session_id``.

        ``extra`` is an optional dict of additional fields (e.g. trace, reasoning,
        route) merged into the stored record for rich snapshot support.
        """
        raise NotImplementedError

    def clear(self, session_id: str) -> None:
        """Remove all stored history for ``session_id``.

        Optional — default is a no-op. Provided for a future "delete conversation"
        feature; backends may override.
        """
        return None

    def load_summary(self, session_id: str) -> str:
        """Return the latest rolling summary for ``session_id``, or ``""`` if none exists.

        Optional — default returns empty string (no-op for backends that don't support it).
        """
        return ""

    def save_summary(self, session_id: str, summary: str) -> None:
        """Persist a new rolling summary for ``session_id``.

        Optional — default is a no-op. Backends that support summaries should override.
        """
        return None
