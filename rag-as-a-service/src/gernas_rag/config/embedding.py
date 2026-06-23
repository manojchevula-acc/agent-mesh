"""Embedding configuration."""

from enum import Enum

from pydantic import BaseModel


class EmbeddingProvider(str, Enum):
    BGEM3 = "bgem3"  # BAAI/bge-m3 — primary
    SENTENCE_TRANSFORMER = "sentence_transformer"
    OPENAI_COMPAT = "openai_compat"  # Any OpenAI-compatible embedding API


class EmbeddingConfig(BaseModel):
    provider: EmbeddingProvider = EmbeddingProvider.BGEM3
    model_name: str = "BAAI/bge-m3"
    device: str = "cpu"  # 'cpu' | 'cuda' | 'mps'
    use_fp16: bool = True
    batch_size: int = 32  # Chunks per encoding batch
    max_length: int = 512  # Max tokens per chunk encoding
    dense_dim: int = 1024  # Output dense vector dimension
    return_sparse: bool = True  # Produce SPLADE sparse vectors
    normalize_embeddings: bool = True

    # For OPENAI_COMPAT provider:
    openai_base_url: str | None = None
    openai_api_key: str | None = None


class RerankerConfig(BaseModel):
    enabled: bool = True
    model_name: str = "BAAI/bge-reranker-v2-m3"
    device: str = "cpu"
    use_fp16: bool = True
    top_n: int = 5  # Return top-N after reranking
    normalize: bool = True
