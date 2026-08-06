"""Content-addressed embedding cache.

Keyed by (space_id, sha256(content)). Because the key includes ``space_id``, a
model swap can never return stale vectors from a different space — the cache
simply misses. Backed by the Redis instance the service already runs.

Impact: re-ingesting an unchanged document costs ~0 embedding time, which is the
highest-leverage performance feature in the multimodal pipeline because image
embedding dominates ingestion cost.
"""

import hashlib
import json
from typing import Any

from ...utils.logging import get_logger

logger = get_logger(__name__)


class EmbeddingCache:
    def __init__(
        self,
        redis_cache: Any,
        space_id: str,
        ttl_seconds: int = 604800,
        enabled: bool = True,
    ) -> None:
        self._cache = redis_cache
        self._space_id = space_id
        self._ttl = ttl_seconds
        self._enabled = enabled and redis_cache is not None

    @staticmethod
    def content_hash(data: bytes | str) -> str:
        raw = data.encode() if isinstance(data, str) else data
        return hashlib.sha256(raw).hexdigest()

    def _key(self, content_hash: str) -> str:
        return f"emb:{self._space_id}:{content_hash}"

    async def get_many(self, hashes: list[str]) -> dict[str, list[float]]:
        if not self._enabled or not hashes:
            return {}
        found: dict[str, list[float]] = {}
        for h in hashes:
            try:
                raw = await self._cache.get(self._key(h))
            except Exception as exc:  # noqa: BLE001 - a cache miss is always safe
                logger.warning("Embedding cache read failed", error=str(exc))
                return found
            if raw:
                try:
                    found[h] = json.loads(raw)
                except (TypeError, ValueError):
                    continue
        return found

    async def set_many(self, items: dict[str, list[float]]) -> None:
        if not self._enabled or not items:
            return
        for h, vector in items.items():
            try:
                await self._cache.set(self._key(h), json.dumps(vector))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Embedding cache write failed", error=str(exc))
                return
