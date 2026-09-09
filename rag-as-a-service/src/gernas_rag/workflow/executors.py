"""Retrieval pipeline stages as MAF Executors.

Each executor is one stage of the retrieval flow. They read from and write to a
shared ``RagState`` and hand it on with ``await ctx.send_message(state)``; the
edge chain in ``build.py`` fixes the order.

Components (searcher, reranker, freshness filter, embedder, vectordb) are
**injected**, never constructed here. They are the same instances
``RetrievalPipeline`` holds, which is what keeps ``eval/stage3_retrieval/runner.py``
— which reaches into those private attributes to drive the production components —
working unchanged. See MAF_MIGRATION_PLAN.md §4.2.

MAF context typing (confirmed against agent_framework 1.17.0):
  * an executor that only forwards          -> ``WorkflowContext[RagState]``
    and ``await ctx.send_message(state)``
  * a terminal executor named in ``output_from`` -> ``WorkflowContext[Never, RagState]``
    and ``await ctx.yield_output(state)``
An output executor declared without the second type parameter fails at
``build()`` with ``WorkflowValidationError: must have output type annotations``.
"""

import time
from typing import Never

from agent_framework import Executor, WorkflowContext, handler

from ..embeddings.base import BaseEmbedder
from ..models.retrieval import RetrievedChunk, RetrieveResponse
from ..retrieval.freshness import FreshnessFilter
from ..retrieval.hybrid_search import HybridSearcher
# Imported rather than redefined so this module and the retrieval domain cannot
# drift apart on the threshold that decides `freshness_warning` for every chunk.
# No import cycle: pipeline.py imports this package lazily, inside
# RetrievalPipeline.__init__, never at module level.
from ..retrieval.pipeline import _FRESHNESS_WARNING_THRESHOLD
from ..retrieval.reranker import Reranker
from ..utils.logging import get_logger
from ..vectordb.base import BaseVectorDB
from .state import RagState

logger = get_logger(__name__)


class EncodeExecutor(Executor):
    """Step 1 — encode the query (dense + sparse).

    Calls ``embed_query`` and unpacks the dense/sparse vectors that
    ``HybridSearchExecutor`` needs. Also stamps ``started_at``, which
    ``AssembleResponseExecutor`` reads to report end-to-end latency.
    """

    def __init__(self, embedder: BaseEmbedder, id: str = "encode") -> None:
        super().__init__(id=id)
        self._embedder = embedder

    @handler
    async def run(self, state: RagState, ctx: WorkflowContext[RagState]) -> None:
        state.started_at = time.perf_counter()

        embedding = await self._embedder.embed_query(state.request.query)
        state.dense_vector = embedding.dense_vectors[0]
        state.sparse_indices = embedding.sparse_indices[0] if embedding.sparse_indices else []
        state.sparse_values = embedding.sparse_values[0] if embedding.sparse_values else []

        await ctx.send_message(state)


class HybridSearchExecutor(Executor):
    """Step 2 — dense ANN + sparse BM25, merged with RRF.

    ``HybridSearcher`` already runs the two searches concurrently via
    ``asyncio.gather`` and performs the RRF merge internally — none of that moves.
    This executor only supplies arguments.
    """

    def __init__(
        self, searcher: HybridSearcher, pre_rerank_top_k: int, id: str = "hybrid_search"
    ) -> None:
        super().__init__(id=id)
        self._searcher = searcher
        self._pre_rerank_top_k = pre_rerank_top_k

    @handler
    async def run(self, state: RagState, ctx: WorkflowContext[RagState]) -> None:
        state.candidates = await self._searcher.search(
            dense_vector=state.dense_vector,
            sparse_indices=state.sparse_indices,
            sparse_values=state.sparse_values,
            filters=state.request.filters,
            pre_rerank_top_k=self._pre_rerank_top_k,
        )
        await ctx.send_message(state)


class RerankExecutor(Executor):
    """Step 3 — cross-encoder rerank, or plain truncation.

    Falls back to plain truncation when no reranker is configured.

    The ``if self._reranker and candidates`` test stays **inside** the executor
    rather than becoming a MAF conditional edge, because the reranker also fails
    soft internally (``reranker.py`` sets ``_unavailable`` and truncates on a load
    or scoring failure) — so the decision is not purely edge-level, and splitting
    it across both places would put the same fallback in two spots.
    """

    def __init__(self, reranker: Reranker | None, id: str = "rerank") -> None:
        super().__init__(id=id)
        self._reranker = reranker

    @handler
    async def run(self, state: RagState, ctx: WorkflowContext[RagState]) -> None:
        top_k = state.request.top_k
        if self._reranker and state.candidates:
            state.candidates = await self._reranker.rerank(
                query=state.request.query, results=state.candidates, top_n=top_k
            )
        else:
            state.candidates = state.candidates[:top_k]
        await ctx.send_message(state)


class FreshnessExecutor(Executor):
    """Step 4 — apply the staleness penalty.

    ``FreshnessFilter.apply`` is synchronous, returns a new list, re-sorts by
    penalised score and rewrites ``rank``. Unchanged — this executor only calls it.
    """

    def __init__(self, freshness: FreshnessFilter, id: str = "freshness") -> None:
        super().__init__(id=id)
        self._freshness = freshness

    @handler
    async def run(self, state: RagState, ctx: WorkflowContext[RagState]) -> None:
        state.candidates = self._freshness.apply(state.candidates)
        await ctx.send_message(state)


class ParentExpandExecutor(Executor):
    """Step 5 — small-to-big parent chunk expansion.

    De-duplicates parent ids before the lookup, and leaves ``parent_map`` empty
    when ``include_parent`` is False.
    """

    def __init__(self, vectordb: BaseVectorDB, id: str = "parent_expand") -> None:
        super().__init__(id=id)
        self._vectordb = vectordb

    @handler
    async def run(self, state: RagState, ctx: WorkflowContext[RagState]) -> None:
        if state.request.include_parent:
            parent_ids = [
                r.metadata.get("parent_chunk_id")
                for r in state.candidates
                if r.metadata.get("parent_chunk_id")
            ]
            parents = (
                await self._vectordb.get_by_ids(list(set(filter(None, parent_ids))))
                if parent_ids
                else []
            )
            state.parent_map = {p.id: p.text for p in parents}
        else:
            state.parent_map = {}
        await ctx.send_message(state)


class AssembleResponseExecutor(Executor):
    """Terminal — build the RetrieveResponse.

    Computes end-to-end latency, maps each SearchResult to a RetrievedChunk
    (carrying modality/artifact_ref through for multimodal citations), and
    derives ``freshness_warning_global``.

    Named in ``output_from=[...]``, so the ``RagState`` it yields is what
    ``events.get_outputs()`` returns to the façade.
    """

    @handler
    async def run(self, state: RagState, ctx: WorkflowContext[Never, RagState]) -> None:
        latency_ms = (time.perf_counter() - state.started_at) * 1000
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
                parent_text=state.parent_map.get(r.metadata.get("parent_chunk_id", ""), None),
                modality=r.metadata.get("modality", "text"),
                artifact_ref=r.metadata.get("artifact_ref"),
            )
            for r in state.candidates
        ]
        freshness_global = any(c.freshness_warning for c in chunks)
        logger.info(
            "Retrieval complete",
            query=state.request.query[:80],
            results=len(chunks),
            latency_ms=round(latency_ms, 2),
        )
        state.response = RetrieveResponse(
            chunks=chunks,
            total_results=len(chunks),
            latency_ms=round(latency_ms, 2),
            freshness_warning_global=freshness_global,
        )
        await ctx.yield_output(state)
