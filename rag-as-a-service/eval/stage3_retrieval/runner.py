"""Run the served retrieval path once per question and record the ranked list.

This drives ``RetrievalPipeline``'s *own* searcher, reranker and freshness
filter — same configuration, same weights, one load — so what is recorded is
what production returns, not a re-implementation of it.

Deliberately one pass, not five. An earlier version replayed retrieval
component-by-component (dense alone, sparse alone, fused, reranked) and then
called the real pipeline again to verify the replay, which cost two query
embeddings, six vector searches and two cross-encoder passes per question. On
CPU the cross-encoder dominates, so that made a correctness check several times
more expensive than the thing it was checking. Attribution belongs in a
debugging session on the handful of questions that actually fail, not in every
run.

Parent expansion (pipeline step 5) is not reproduced: it attaches
``parent_text`` without reordering anything, so it cannot move a ranking metric.
"""

from __future__ import annotations

import time

from gernas_rag.config.settings import Settings
from gernas_rag.embeddings.base import BaseEmbedder
from gernas_rag.models.retrieval import DocumentFilter
from gernas_rag.retrieval.pipeline import RetrievalPipeline
from gernas_rag.vectordb.base import BaseVectorDB, SearchResult

from ..core.models import QrelQuestion, RankedHit, RetrievalRunRecord


def _to_hits(results: list[SearchResult]) -> list[RankedHit]:
    return [
        RankedHit(
            chunk_id=r.chunk_id,
            rank=i,
            score=float(r.score),
            document=r.metadata.get("document_name", ""),
            modality=r.metadata.get("modality", "text") or "text",
            clause_reference=r.metadata.get("clause_reference", "") or "",
            is_parent=bool(r.metadata.get("is_parent", False)),
            text=r.text or "",
        )
        for i, r in enumerate(results)
    ]


class RetrievalRunner:
    """Executes the production retrieval path for one configured pipeline."""

    def __init__(
        self,
        settings: Settings,
        embedder: BaseEmbedder,
        vectordb: BaseVectorDB,
        rank_depth: int = 10,
    ) -> None:
        self._settings = settings
        self._embedder = embedder
        # Built once; its components are the ones driven below.
        self._pipeline = RetrievalPipeline(settings, embedder, vectordb)
        self._vectordb = vectordb
        # Deeper than production's final_top_k so recall@10 is measurable. The
        # reranker only truncates, so the first `final_top_k` entries are
        # identical to what production would return.
        self.rank_depth = max(rank_depth, settings.retrieval.final_top_k)

    async def run(self, question: QrelQuestion) -> RetrievalRunRecord:
        query = question.question
        filters = DocumentFilter()
        config = self._settings.retrieval
        start = time.perf_counter()

        embedding = await self._embedder.embed_query(query)
        dense_vector = embedding.dense_vectors[0]
        sparse_indices = embedding.sparse_indices[0] if embedding.sparse_indices else []
        sparse_values = embedding.sparse_values[0] if embedding.sparse_values else []

        # Hybrid search: dense + sparse in parallel, fused by RRF.
        fused = await self._pipeline._searcher.search(  # noqa: SLF001 — production instance.
            dense_vector=dense_vector,
            sparse_indices=sparse_indices,
            sparse_values=sparse_values,
            filters=filters,
            pre_rerank_top_k=config.pre_rerank_top_k,
        )

        reranker = self._pipeline._reranker  # noqa: SLF001
        if reranker and fused:
            reranked = await reranker.rerank(query=query, results=fused, top_n=self.rank_depth)
        else:
            reranked = fused[: self.rank_depth]

        final = self._pipeline._freshness.apply(list(reranked))  # noqa: SLF001
        return RetrievalRunRecord(
            id=question.id,
            question=query,
            hits=_to_hits(final),
            latency_ms=round((time.perf_counter() - start) * 1000, 2),
        )
