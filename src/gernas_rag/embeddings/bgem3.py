"""Primary embedder — BAAI/bge-m3 (dense + SPLADE sparse)."""

import asyncio
from functools import partial
from typing import Any

from ..config.embedding import EmbeddingConfig
from ..utils.logging import get_logger
from .base import BaseEmbedder, EmbeddingOutput

logger = get_logger(__name__)


class BGEM3Embedder(BaseEmbedder):
    """BAAI/bge-m3 embedder.

    Produces dense (1024-dim) + SPLADE sparse vectors from a single model pass.
    Self-hosted, Apache 2.0 licence.

    NOTE: FlagEmbedding is CPU/GPU bound. All model calls are dispatched to a
    ThreadPoolExecutor via ``asyncio.run_in_executor`` to avoid blocking the event
    loop.
    """

    def __init__(self, config: EmbeddingConfig) -> None:
        self._config = config
        self._model: Any | None = None
        logger.info(
            "Initialising BGE-M3 embedder",
            model=config.model_name,
            device=config.device,
        )

    def _load_model(self) -> None:
        """Lazy load — only called on first use."""
        if self._model is None:
            from FlagEmbedding import BGEM3FlagModel

            self._model = BGEM3FlagModel(
                self._config.model_name,
                use_fp16=self._config.use_fp16,
                device=self._config.device,
            )

    def _sync_encode(self, texts: list[str]) -> EmbeddingOutput:
        """Synchronous encode — runs in thread pool."""
        self._load_model()
        assert self._model is not None
        outputs = self._model.encode(
            sentences=texts,
            batch_size=self._config.batch_size,
            max_length=self._config.max_length,
            return_dense=True,
            return_sparse=self._config.return_sparse,
            return_colbert_vecs=False,
        )
        dense = [v.tolist() for v in outputs["dense_vecs"]]
        sparse_i: list[list[int]] = []
        sparse_v: list[list[float]] = []
        if self._config.return_sparse:
            for weights in outputs["lexical_weights"]:
                # lexical_weights keys are token ids (possibly str); cast to int.
                sparse_i.append([int(k) for k in weights.keys()])
                sparse_v.append([float(v) for v in weights.values()])
        return EmbeddingOutput(
            dense_vectors=dense,
            sparse_indices=sparse_i,
            sparse_values=sparse_v,
        )

    async def embed_documents(self, texts: list[str]) -> EmbeddingOutput:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(self._sync_encode, texts))

    async def embed_query(self, text: str) -> EmbeddingOutput:
        return await self.embed_documents([text])

    @property
    def dense_dim(self) -> int:
        return self._config.dense_dim

    @property
    def supports_sparse(self) -> bool:
        return self._config.return_sparse
