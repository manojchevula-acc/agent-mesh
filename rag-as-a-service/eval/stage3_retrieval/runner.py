"""Execute retrieval one pipeline stage at a time and record the ranked lists.

The ablation reuses ``RetrievalPipeline``'s *own* searcher, reranker and
freshness filter rather than constructing new ones: same configuration, same
model weights, one load. Only the orchestration is reproduced here — and that
reproduction is verified against a real ``RetrievalPipeline.retrieve()`` call per
question, so if the pipeline ever changes shape the parity metric fails instead
of the ablation quietly measuring something else.

Parent expansion (pipeline step 5) is not reproduced because it attaches
``parent_text`` without reordering anything; it cannot affect a ranking metric.
"""

from __future__ import annotations

import time

from gernas_rag.config.settings import Settings
from gernas_rag.embeddings.base import BaseEmbedder
from gernas_rag.models.retrieval import DocumentFilter, RetrieveRequest
from gernas_rag.retrieval.pipeline import RetrievalPipeline
from gernas_rag.vectordb.base import BaseVectorDB, SearchResult

from ..core.models import QrelQuestion, RankedHit, RetrievalRunRecord, RetrievalStageResult

# Ordered coarse-to-fine; each stage's output is the next stage's input.
STAGE_ORDER = ("dense", "sparse", "rrf", "rerank", "final")


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
        )
        for i, r in enumerate(results)
    ]


def _signature(items: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    return items


class AblationRunner:
    """Runs the per-stage ablation for one configured pipeline."""

    def __init__(
        self,
        settings: Settings,
        embedder: BaseEmbedder,
        vectordb: BaseVectorDB,
        rank_depth: int = 10,
    ) -> None:
        self._settings = settings
        self._embedder = embedder
        # Built once; its components are the ones the ablation drives.
        self._pipeline = RetrievalPipeline(settings, embedder, vectordb)
        self._vectordb = vectordb
        # Deeper than production's final_top_k so recall@10 is measurable. The
        # reranker only truncates, so the first `final_top_k` entries are
        # identical to what production would return.
        self.rank_depth = max(rank_depth, settings.retrieval.final_top_k)

    async def run(self, question: QrelQuestion, check_parity: bool = True) -> RetrievalRunRecord:
        query = question.question
        filters = DocumentFilter()
        config = self._settings.retrieval
        stages: dict[str, RetrievalStageResult] = {}

        embedding = await self._embedder.embed_query(query)
        dense_vector = embedding.dense_vectors[0]
        sparse_indices = embedding.sparse_indices[0] if embedding.sparse_indices else []
        sparse_values = embedding.sparse_values[0] if embedding.sparse_values else []

        # ── 1. Dense ANN only ─────────────────────────────────────────
        start = time.perf_counter()
        dense = await self._vectordb.dense_search(dense_vector, config.dense_top_k, filters)
        stages["dense"] = RetrievalStageResult(
            stage="dense", hits=_to_hits(dense), latency_ms=_elapsed(start)
        )

        # ── 2. Sparse BM25 only ───────────────────────────────────────
        start = time.perf_counter()
        sparse = (
            await self._vectordb.sparse_search(
                sparse_indices, sparse_values, config.sparse_top_k, filters
            )
            if sparse_indices
            else []
        )
        stages["sparse"] = RetrievalStageResult(
            stage="sparse", hits=_to_hits(sparse), latency_ms=_elapsed(start)
        )

        # ── 3. RRF fusion (the pipeline's own searcher) ───────────────
        start = time.perf_counter()
        fused = await self._pipeline._searcher.search(  # noqa: SLF001 — same instance as production.
            dense_vector=dense_vector,
            sparse_indices=sparse_indices,
            sparse_values=sparse_values,
            filters=filters,
            pre_rerank_top_k=config.pre_rerank_top_k,
        )
        stages["rrf"] = RetrievalStageResult(
            stage="rrf", hits=_to_hits(fused), latency_ms=_elapsed(start)
        )

        # ── 4. Cross-encoder rerank ───────────────────────────────────
        start = time.perf_counter()
        reranker = self._pipeline._reranker  # noqa: SLF001
        if reranker and fused:
            reranked = await reranker.rerank(query=query, results=fused, top_n=self.rank_depth)
        else:
            reranked = fused[: self.rank_depth]
        stages["rerank"] = RetrievalStageResult(
            stage="rerank", hits=_to_hits(reranked), latency_ms=_elapsed(start)
        )

        # ── 5. Freshness penalty -> the ordering production serves ────
        start = time.perf_counter()
        final = self._pipeline._freshness.apply(list(reranked))  # noqa: SLF001
        stages["final"] = RetrievalStageResult(
            stage="final", hits=_to_hits(final), latency_ms=_elapsed(start)
        )

        parity: bool | None = None
        if check_parity:
            parity = await self._check_parity(query, final)

        return RetrievalRunRecord(id=question.id, question=query, stages=stages, pipeline_parity=parity)

    async def _check_parity(self, query: str, final: list[SearchResult]) -> bool:
        """Confirm the reproduced ordering matches what the real pipeline returns.

        ``RetrievedChunk`` carries no chunk id, so the comparison is on the
        (document, clause, text-prefix) triple, which is sufficient to detect a
        reordering or a different result set.
        """
        top_k = self._settings.retrieval.final_top_k
        response = await self._pipeline.retrieve(
            RetrieveRequest(query=query, top_k=top_k, include_parent=False, generate_answer=False)
        )
        produced = _signature(
            [(c.source, c.clause_reference, c.text[:120]) for c in response.chunks]
        )
        reproduced = _signature(
            [
                (
                    r.metadata.get("document_name", ""),
                    r.metadata.get("clause_reference", "") or "",
                    r.text[:120],
                )
                for r in final[:top_k]
            ]
        )
        return produced == reproduced


def _elapsed(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)
