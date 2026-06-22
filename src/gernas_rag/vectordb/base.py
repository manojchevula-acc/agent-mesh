"""Vector database abstract base class."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..models.chunk import Chunk, EmbeddedChunk
from ..models.retrieval import DocumentFilter


@dataclass
class SearchResult:
    chunk_id: str
    text: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)
    rank: int = 0


class BaseVectorDB(ABC):
    @abstractmethod
    async def create_collection(self, name: str, dense_dim: int) -> None: ...

    @abstractmethod
    async def upsert(self, chunks: list[EmbeddedChunk]) -> int:
        """Upsert chunks. Returns count of upserted records."""
        ...

    @abstractmethod
    async def dense_search(
        self,
        query_vector: list[float],
        top_k: int,
        filters: DocumentFilter | None = None,
    ) -> list[SearchResult]: ...

    @abstractmethod
    async def sparse_search(
        self,
        query_indices: list[int],
        query_values: list[float],
        top_k: int,
        filters: DocumentFilter | None = None,
    ) -> list[SearchResult]: ...

    @abstractmethod
    async def get_by_ids(self, ids: list[str]) -> list[Chunk]: ...

    @abstractmethod
    async def delete_collection(self, name: str) -> None: ...

    @abstractmethod
    async def health_check(self) -> bool: ...
