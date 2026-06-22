"""Embedding model abstraction."""

from .base import BaseEmbedder, EmbeddingOutput
from .factory import get_embedder

__all__ = ["BaseEmbedder", "EmbeddingOutput", "get_embedder"]
