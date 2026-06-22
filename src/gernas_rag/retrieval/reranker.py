"""Reranker — cross-encoder reranking with BAAI/bge-reranker-v2-m3."""

import asyncio
from functools import partial
from typing import Any

from ..config.embedding import RerankerConfig
from ..utils.logging import get_logger
from ..vectordb.base import SearchResult

logger = get_logger(__name__)


class Reranker:
    """Cross-encoder reranker.

    Accepts the embedding config (for device / fp16 settings) and builds a
    :class:`RerankerConfig` for the reranker model. The model is CPU/GPU bound, so
    scoring runs in a thread pool executor.
    """

    def __init__(self, config: RerankerConfig | None = None) -> None:
        self._config = config or RerankerConfig()
        self._model: Any | None = None
        self._unavailable = False  # Set if the model cannot be loaded.

    @classmethod
    def from_embedding_config(cls, embedding_config: Any) -> "Reranker":
        """Build a reranker that reuses the embedding device / fp16 settings."""
        return cls(
            RerankerConfig(
                device=getattr(embedding_config, "device", "cpu"),
                use_fp16=getattr(embedding_config, "use_fp16", True),
            )
        )

    def _load(self) -> None:
        if self._model is None:
            from FlagEmbedding import FlagReranker

            self._model = FlagReranker(
                self._config.model_name, use_fp16=self._config.use_fp16, device=self._config.device
            )

    def _sync_rerank(
        self, query: str, results: list[SearchResult], top_n: int
    ) -> list[SearchResult]:
        self._load()
        assert self._model is not None
        pairs = [[query, r.text] for r in results]
        scores = self._model.compute_score(pairs, normalize=self._config.normalize)
        if not isinstance(scores, list):
            scores = [scores]
        ranked = sorted(zip(results, scores), key=lambda x: x[1], reverse=True)[:top_n]
        return [
            SearchResult(r.chunk_id, r.text, float(score), r.metadata, i)
            for i, (r, score) in enumerate(ranked)
        ]

    async def rerank(
        self, query: str, results: list[SearchResult], top_n: int
    ) -> list[SearchResult]:
        if not results or self._unavailable:
            return results[:top_n]
        loop = asyncio.get_running_loop()
        try:
            reranked = await loop.run_in_executor(
                None, partial(self._sync_rerank, query, results, top_n)
            )
        except Exception as exc:
            # The reranker is an enhancement, not a hard dependency. If the model
            # cannot be loaded or scored (e.g. FlagEmbedding missing), degrade
            # gracefully to fused-rank truncation rather than failing retrieval.
            self._unavailable = True
            logger.warning(
                "Reranker unavailable; falling back to fused ranking",
                error=str(exc),
                model=self._config.model_name,
            )
            return results[:top_n]
        logger.info("Reranking complete", candidates=len(results), returned=len(reranked))
        return reranked
