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

    # Deliberately NOT abstract: adding an abstract method here would break
    # every existing implementation, including the test fakes.
    async def reconcile_document(
        self, document_name: str, keep_chunk_ids: list[str]
    ) -> int:
        """Drop points for *document_name* whose chunk id is not in *keep*.

        Re-ingestion is only idempotent while chunk ids are stable. Any change
        to chunk boundaries — enabling table protection, or a non-deterministic
        extractor such as Docling under memory pressure — produces new ids, and
        the previous run's points linger forever because upsert never deletes.

        Returns the number of stale points removed. The default implementation
        is a no-op for backends that have not implemented it.
        """
        return 0
