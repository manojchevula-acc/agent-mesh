"""Dense-only embedder backed by sentence-transformers."""

import asyncio
from functools import partial
from typing import Any

from ..config.embedding import EmbeddingConfig
from ..utils.logging import get_logger
from .base import BaseEmbedder, EmbeddingOutput

logger = get_logger(__name__)


class SentenceTransformerEmbedder(BaseEmbedder):
    """Dense-only embedder. Produces no sparse vectors.

    The model is CPU/GPU bound, so encoding runs in a thread pool executor.
    """

    def __init__(self, config: EmbeddingConfig) -> None:
        self._config = config
        self._model: Any | None = None
        self._dim: int = config.dense_dim
        logger.info(
            "Initialising SentenceTransformer embedder",
            model=config.model_name,
            device=config.device,
        )

    def _load_model(self) -> None:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(
                self._config.model_name, device=self._config.device
            )
            self._dim = int(self._model.get_sentence_embedding_dimension())

    def _sync_encode(self, texts: list[str]) -> EmbeddingOutput:
        self._load_model()
        assert self._model is not None
        vectors = self._model.encode(
            texts,
            batch_size=self._config.batch_size,
            normalize_embeddings=self._config.normalize_embeddings,
            convert_to_numpy=True,
        )
        dense = [v.tolist() for v in vectors]
        return EmbeddingOutput(dense_vectors=dense, sparse_indices=[], sparse_values=[])

    async def embed_documents(self, texts: list[str]) -> EmbeddingOutput:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(self._sync_encode, texts))

    async def embed_query(self, text: str) -> EmbeddingOutput:
        return await self.embed_documents([text])

    @property
    def dense_dim(self) -> int:
        return self._dim

    @property
    def supports_sparse(self) -> bool:
        return False
