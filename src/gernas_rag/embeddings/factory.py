"""Embedder factory."""

from ..config.embedding import EmbeddingConfig, EmbeddingProvider
from .base import BaseEmbedder


def get_embedder(config: EmbeddingConfig) -> BaseEmbedder:
    match config.provider:
        case EmbeddingProvider.BGEM3:
            from .bgem3 import BGEM3Embedder

            return BGEM3Embedder(config)
        case EmbeddingProvider.SENTENCE_TRANSFORMER:
            from .sentence_transformer import SentenceTransformerEmbedder

            return SentenceTransformerEmbedder(config)
        case _:
            raise ValueError(f"Unsupported embedding provider: {config.provider}")
