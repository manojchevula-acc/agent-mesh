"""Redis conversation backend — PLACEHOLDER STUB (not yet active).

This is a scaffold for a future Redis-backed conversation store. It intentionally
implements the :class:`ConversationBackend` interface but is NOT wired for
production use yet — selecting it (``CONVERSATION_BACKEND=redis``) raises a clear
error so the swap point is obvious without silently losing history.

Intended design when implemented
--------------------------------
Store each session as a Redis list and read/write with native list ops::

    key   = f"conv:{session_id}"
    write = RPUSH key  <json message>        # append one message
    read  = LRANGE key 0 -1                   # full history in order
    clear = DEL key
    # optionally: EXPIRE key <ttl> for auto-expiring demo sessions

Redis is already available in the wider stack (rag-as-a-service uses it for
caching), so this becomes the natural multi-node backend. Flipping
``Config.CONVERSATION_BACKEND`` to ``"redis"`` (once the TODOs below are done) is
the only change required — the orchestrator/workflow are backend-agnostic.
"""
from __future__ import annotations

from typing import List, Dict

from src.config import Config
from src.memory.base import ConversationBackend


class RedisBackend(ConversationBackend):
    """Future Redis-backed store. Placeholder — see module docstring."""

    def __init__(self, url: str | None = None) -> None:
        self._url = url or Config.CONVERSATION_REDIS_URL
        self._client = None  # lazily created in _connect()

    def _connect(self):
        """Lazily create a Redis client.

        Guarded import so the optional ``redis`` dependency is only required when
        this backend is actually selected.
        """
        if self._client is not None:
            return self._client
        # TODO: enable once the Redis backend is adopted.
        #   import redis
        #   self._client = redis.Redis.from_url(self._url, decode_responses=True)
        #   return self._client
        raise NotImplementedError(
            "Redis conversation backend is not yet wired. "
            "Set CONVERSATION_BACKEND=jsonl (default) to use the active file-based "
            "store, or implement RedisBackend (src/memory/redis_backend.py)."
        )

    def _key(self, session_id: str) -> str:
        return f"conv:{session_id}"

    def load_messages(self, session_id: str) -> List[Dict]:
        # TODO: client = self._connect()
        #       raw = client.lrange(self._key(session_id), 0, -1)
        #       return [json.loads(r) for r in raw]
        self._connect()  # raises NotImplementedError until implemented
        return []

    def append(self, session_id: str, role: str, content: str) -> None:
        # TODO: client = self._connect()
        #       rec = {"role": role, "content": content, "ts": <iso-now>}
        #       client.rpush(self._key(session_id), json.dumps(rec))
        self._connect()  # raises NotImplementedError until implemented

    def clear(self, session_id: str) -> None:
        # TODO: self._connect().delete(self._key(session_id))
        self._connect()  # raises NotImplementedError until implemented
