"""RetrievalPipeline — hybrid search + rerank + freshness + parent expansion."""

import time

from ..config.embedding import EmbeddingProvider
from ..config.settings import Settings
from ..embeddings.base import BaseEmbedder
from ..models.retrieval import RetrievedChunk, RetrieveRequest, RetrieveResponse
from ..utils.logging import get_logger
from ..vectordb.base import BaseVectorDB
from .freshness import FreshnessFilter
from .hybrid_search import HybridSearcher
from .reranker import Reranker

logger = get_logger(__name__)

_FRESHNESS_WARNING_THRESHOLD = 0.7


class RetrievalPipeline:
    """Full retrieval pipeline:

    ``Query → Encode → Metadata filter → Dense ANN + Sparse BM25 → RRF merge →
    Cross-encoder rerank → Freshness penalty → Parent expand``
    """

    def __init__(self, settings: Settings, embedder: BaseEmbedder, vectordb: BaseVectorDB) -> None:
        self._settings = settings
        self._embedder = embedder
        self._vectordb = vectordb
        self._searcher = HybridSearcher(settings.retrieval, vectordb)
        self._reranker = (
            Reranker.from_embedding_config(settings.embedding)
            if settings.embedding.provider != EmbeddingProvider.SENTENCE_TRANSFORMER
            else None
        )
        self._freshness = FreshnessFilter(settings.retrieval)

    async def retrieve(self, request: RetrieveRequest) -> RetrieveResponse:
        start = time.perf_counter()

        # Step 1: Encode query (dense + sparse)
        embedding = await self._embedder.embed_query(request.query)

        # Step 2: Hybrid search (dense ANN + sparse BM25 + RRF merge)
        candidates = await self._searcher.search(
            dense_vector=embedding.dense_vectors[0],
            sparse_indices=embedding.sparse_indices[0] if embedding.sparse_indices else [],
            sparse_values=embedding.sparse_values[0] if embedding.sparse_values else [],
            filters=request.filters,
            pre_rerank_top_k=self._settings.retrieval.pre_rerank_top_k,
        )

        # Step 3: Cross-encoder rerank
        if self._reranker and candidates:
            candidates = await self._reranker.rerank(
                query=request.query, results=candidates, top_n=request.top_k
            )
        else:
            candidates = candidates[: request.top_k]

        # Step 4: Freshness penalty
        candidates = self._freshness.apply(candidates)

        # Step 5: Parent chunk expansion
        if request.include_parent:
            parent_ids = [
                r.metadata.get("parent_chunk_id")
                for r in candidates
                if r.metadata.get("parent_chunk_id")
            ]
            parents = (
                await self._vectordb.get_by_ids(list(set(filter(None, parent_ids))))
                if parent_ids
                else []
            )
            parent_map = {p.id: p.text for p in parents}
        else:
            parent_map = {}

        # Build response
        latency_ms = (time.perf_counter() - start) * 1000
        chunks = [
            RetrievedChunk(
                text=r.text,
                source=r.metadata.get("document_name", ""),
                section_heading=r.metadata.get("section_heading", ""),
                clause_reference=r.metadata.get("clause_reference", ""),
                score=r.score,
                effective_date=r.metadata.get("effective_date", ""),
                freshness_warning=r.metadata.get("freshness_score", 1.0)
                < _FRESHNESS_WARNING_THRESHOLD,
                parent_text=parent_map.get(r.metadata.get("parent_chunk_id", ""), None),
            )
            for r in candidates
        ]
        freshness_global = any(c.freshness_warning for c in chunks)
        logger.info(
            "Retrieval complete",
            query=request.query[:80],
            results=len(chunks),
            latency_ms=round(latency_ms, 2),
        )
        return RetrieveResponse(
            chunks=chunks,
            total_results=len(chunks),
            latency_ms=round(latency_ms, 2),
            freshness_warning_global=freshness_global,
        )
