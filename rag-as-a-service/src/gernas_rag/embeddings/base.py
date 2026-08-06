"""Embedder abstract base class."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class EmbeddingOutput:
    dense_vectors: list[list[float]]
    sparse_indices: list[list[int]] = field(default_factory=list)  # Empty if no sparse support
    sparse_values: list[list[float]] = field(default_factory=list)


@dataclass(frozen=True)
class EmbeddingSpace:
    """Identity of a vector space.

    Two vectors are comparable if and only if they share a ``space_id``. The id
    is folded into the collection name so a model swap can never silently query
    an index built with a different encoder.
    """

    space_id: str
    provider: str
    model_name: str
    revision: str | None = None
    dim: int = 0
    metric: str = "cosine"
    normalized: bool = True
    modalities: frozenset[str] = frozenset({"text"})

    def collection_name(self, base: str) -> str:
        from ..utils.hashing import slugify_model

        return f"{base}__{slugify_model(self.model_name)}__d{self.dim}"


class BaseEmbedder(ABC):
    """All embedders must implement this interface."""

    @abstractmethod
    async def embed_documents(self, texts: list[str]) -> EmbeddingOutput:
        """Embed a batch of document chunks for indexing."""
        ...

    @abstractmethod
    async def embed_query(self, text: str) -> EmbeddingOutput:
        """Embed a single query for retrieval."""
        ...

    @property
    @abstractmethod
    def dense_dim(self) -> int:
        """Dimension of the dense vector output."""
        ...

    @property
    @abstractmethod
    def supports_sparse(self) -> bool:
        """Whether this embedder produces sparse vectors."""
        ...
