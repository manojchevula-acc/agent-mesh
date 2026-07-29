"""Search history store — one JSON file per past /retrieve call.

Unlike agent-mesh's JSONL conversation store (built for growing multi-turn
threads, hence append-only files + sidecars), a search history entry is
written once and only ever rewritten whole on rename/never on delete — so a
single JSON file per entry is the right-sized equivalent, following the same
ABC + Local* impl + factory shape as ``artifact_store.py``.
"""

from abc import ABC, abstractmethod
from pathlib import Path
import re

from ..config.search_history import SearchHistoryConfig
from ..models.search_history import SearchHistoryEntry, SearchHistorySummary
from ..utils.logging import get_logger

logger = get_logger(__name__)

# search_id is used as a filename — restrict to a safe charset to prevent path
# traversal or invalid filenames (it arrives via a URL path param).
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


class BaseSearchHistoryStore(ABC):
    @abstractmethod
    async def save(self, entry: SearchHistoryEntry) -> None: ...

    @abstractmethod
    async def get(self, search_id: str) -> SearchHistoryEntry | None:
        """Return the entry, or None if missing/corrupt — never raises."""
        ...

    @abstractmethod
    async def list(self, username: str, limit: int = 200) -> list[SearchHistorySummary]:
        """Return this user's entries, newest first, capped at ``limit``."""
        ...

    @abstractmethod
    async def rename(self, search_id: str, title: str) -> None:
        """Set (or, if blank, clear) the user-defined title. No-op if missing."""
        ...

    @abstractmethod
    async def delete(self, search_id: str) -> None: ...

    def check_owner(self, entry: SearchHistoryEntry, requesting_user: str) -> bool:
        """True if ``requesting_user`` owns ``entry`` (or it predates ownership).

        Backward-compatible: an entry with no recorded username is allowed
        through, mirroring ConversationStore.check_owner.
        """
        if not entry.username:
            return True
        return entry.username == requesting_user


class LocalSearchHistoryStore(BaseSearchHistoryStore):
    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, search_id: str) -> Path:
        safe = _UNSAFE.sub("_", search_id or "unknown")
        return self._root / f"{safe}.json"

    async def save(self, entry: SearchHistoryEntry) -> None:
        self._path_for(entry.search_id).write_text(entry.model_dump_json(), encoding="utf-8")

    async def get(self, search_id: str) -> SearchHistoryEntry | None:
        path = self._path_for(search_id)
        if not path.exists():
            return None
        try:
            return SearchHistoryEntry.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — tolerate a corrupt file, never raise.
            logger.warning("Corrupt search-history entry; skipping", search_id=search_id, error=str(exc))
            return None

    async def list(self, username: str, limit: int = 200) -> list[SearchHistorySummary]:
        summaries: list[SearchHistorySummary] = []
        for path in self._root.glob("*.json"):
            try:
                entry = SearchHistoryEntry.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 — tolerate a corrupt file, skip it.
                continue
            if entry.username != username:
                continue
            summaries.append(
                SearchHistorySummary(
                    search_id=entry.search_id,
                    query=entry.query,
                    title=entry.title,
                    created_at=entry.created_at,
                    top_k=entry.top_k,
                    generate_answer=entry.generate_answer,
                    total_results=entry.response.total_results,
                    cache_hit=entry.response.cache_hit,
                )
            )
        summaries.sort(key=lambda s: s.created_at, reverse=True)
        return summaries[:limit]

    async def rename(self, search_id: str, title: str) -> None:
        entry = await self.get(search_id)
        if entry is None:
            return
        cleaned = (title or "").strip()
        updated = entry.model_copy(update={"title": cleaned or None})
        await self.save(updated)

    async def delete(self, search_id: str) -> None:
        self._path_for(search_id).unlink(missing_ok=True)


def get_search_history_store(config: SearchHistoryConfig) -> BaseSearchHistoryStore:
    if config.backend == "local":
        return LocalSearchHistoryStore(config.local_path)
    raise ValueError(f"Unsupported search_history backend: {config.backend}")
