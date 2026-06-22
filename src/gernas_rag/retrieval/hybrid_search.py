"""HybridSearcher — dense ANN + sparse BM25 run in parallel, merged with RRF."""

import asyncio
from collections import defaultdict

from ..config.retrieval import RetrievalConfig
from ..models.retrieval import DocumentFilter
from ..utils.logging import get_logger
from ..vectordb.base import BaseVectorDB, SearchResult

logger = get_logger(__name__)


class HybridSearcher:
    """Runs dense ANN and sparse BM25 searches in parallel, then merges them with
    Reciprocal Rank Fusion (RRF).
    """

    def __init__(self, config: RetrievalConfig, vectordb: BaseVectorDB) -> None:
        self._config = config
        self._vectordb = vectordb

    async def search(
        self,
        dense_vector: list[float],
        sparse_indices: list[int],
        sparse_values: list[float],
        filters: DocumentFilter | None,
        pre_rerank_top_k: int,
    ) -> list[SearchResult]:
        async def _sparse() -> list[SearchResult]:
            if not sparse_indices:
                return []
            return await self._vectordb.sparse_search(
                sparse_indices, sparse_values, self._config.sparse_top_k, filters
            )

        # Run dense and sparse in parallel.
        dense_results, sparse_results = await asyncio.gather(
            self._vectordb.dense_search(dense_vector, self._config.dense_top_k, filters),
            _sparse(),
        )
        merged = self._rrf_merge(dense_results, sparse_results, pre_rerank_top_k)
        logger.info(
            "Hybrid search complete",
            dense=len(dense_results),
            sparse=len(sparse_results),
            merged=len(merged),
        )
        return merged

    def _rrf_merge(
        self, dense: list[SearchResult], sparse: list[SearchResult], top_k: int
    ) -> list[SearchResult]:
        k = self._config.rrf_k
        scores: dict[str, float] = defaultdict(float)
        result_map: dict[str, SearchResult] = {}

        for rank, r in enumerate(dense):
            scores[r.chunk_id] += 1.0 / (k + rank)
            result_map[r.chunk_id] = r
        for rank, r in enumerate(sparse):
            scores[r.chunk_id] += 1.0 / (k + rank)
            result_map.setdefault(r.chunk_id, r)

        merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        return [
            SearchResult(cid, result_map[cid].text, score, result_map[cid].metadata, i)
            for i, (cid, score) in enumerate(merged)
        ]
