"""Embedder abstract base class."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class EmbeddingOutput:
    dense_vectors: list[list[float]]
    sparse_indices: list[list[int]] = field(default_factory=list)  # Empty if no sparse support
    sparse_values: list[list[float]] = field(default_factory=list)


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
