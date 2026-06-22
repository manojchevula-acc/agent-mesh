"""RAGCache — query result caching backed by Redis / Valkey."""

import hashlib
from typing import Any

from ..models.retrieval import RetrieveRequest
from ..utils.logging import get_logger

logger = get_logger(__name__)

_KEY_PREFIX = "gernas:retrieve:"


class RAGCache:
    """Caches retrieval responses in Redis.

    Every operation is wrapped so that a cache outage degrades gracefully to a
    cache miss rather than failing the request. Disable entirely via ``enabled``.
    """

    def __init__(self, redis_url: str, ttl_seconds: int, enabled: bool = True) -> None:
        self._ttl = ttl_seconds
        self._enabled = enabled
        self._client: Any | None = None
        if enabled:
            try:
                import redis.asyncio as aioredis

                self._client = aioredis.from_url(redis_url, decode_responses=True)
            except Exception as exc:  # pragma: no cover - optional dependency path
                logger.warning("Redis unavailable; caching disabled", error=str(exc))
                self._enabled = False

    @staticmethod
    def make_key(request: RetrieveRequest) -> str:
        payload = request.model_dump_json()
        digest = hashlib.sha256(payload.encode()).hexdigest()
        return f"{_KEY_PREFIX}{digest}"

    async def get(self, key: str) -> str | None:
        if not self._enabled or self._client is None:
            return None
        try:
            return await self._client.get(key)
        except Exception as exc:
            logger.warning("Cache get failed", key=key, error=str(exc))
            return None

    async def set(self, key: str, value: str) -> None:
        if not self._enabled or self._client is None:
            return
        try:
            await self._client.set(key, value, ex=self._ttl)
        except Exception as exc:
            logger.warning("Cache set failed", key=key, error=str(exc))

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception as exc:  # pragma: no cover
                logger.warning("Cache close failed", error=str(exc))
