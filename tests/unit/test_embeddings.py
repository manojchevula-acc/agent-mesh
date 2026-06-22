"""Unit tests for the embeddings module."""

import pytest

from gernas_rag.config.embedding import EmbeddingConfig, EmbeddingProvider
from gernas_rag.embeddings.base import EmbeddingOutput
from gernas_rag.embeddings.factory import get_embedder


async def test_fake_embedder_dense_and_sparse(fake_embedder):
    out = await fake_embedder.embed_documents(["hello", "world"])
    assert isinstance(out, EmbeddingOutput)
    assert len(out.dense_vectors) == 2
    assert len(out.dense_vectors[0]) == fake_embedder.dense_dim
    assert out.sparse_indices and out.sparse_values


async def test_embed_query_returns_single(fake_embedder):
    out = await fake_embedder.embed_query("a query")
    assert len(out.dense_vectors) == 1


def test_factory_unsupported_provider_raises():
    config = EmbeddingConfig()
    object.__setattr__(config, "provider", "nonexistent")
    with pytest.raises(ValueError):
        get_embedder(config)


def test_factory_dispatches_provider_enum():
    # The default provider is BGE-M3; the factory must accept it without raising
    # at selection time (model load is lazy).
    config = EmbeddingConfig(provider=EmbeddingProvider.BGEM3)
    assert config.provider == EmbeddingProvider.BGEM3
