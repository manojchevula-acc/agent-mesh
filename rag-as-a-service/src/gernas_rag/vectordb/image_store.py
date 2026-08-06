"""Image collection contract.

Deliberately SEPARATE from :class:`BaseVectorDB`: every method on
``QdrantVectorDB`` reads ``self._config.collection_name`` and takes no collection
argument. Widening that ABC would break ``FakeVectorDB`` in tests/conftest.py and
both alternative clients. A narrow, purpose-built interface is the lower-risk
change and keeps the text path provably untouched.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..models.asset import EmbeddedImage, ImageAsset
from ..models.retrieval import DocumentFilter


@dataclass
class ImageSearchResult:
    asset_id: str
    score: float
    payload: dict[str, Any] = field(default_factory=dict)
    rank: int = 0


class BaseImageStore(ABC):
    @abstractmethod
    async def create_collection(
        self, name: str, dim: int, metric: str = "cosine"
    ) -> None: ...

    @abstractmethod
    async def upsert_images(self, images: list[EmbeddedImage]) -> int: ...

    @abstractmethod
    async def dense_search(
        self,
        query_vector: list[float],
        top_k: int,
        filters: DocumentFilter | None = None,
    ) -> list[ImageSearchResult]: ...

    @abstractmethod
    async def get_by_ids(self, asset_ids: list[str]) -> list[ImageAsset]: ...

    @abstractmethod
    async def count(self) -> int: ...

    @abstractmethod
    async def delete_by_document(self, document_name: str) -> int: ...

    @abstractmethod
    async def health_check(self) -> bool: ...
