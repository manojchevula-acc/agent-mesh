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

import asyncio
import time
from collections.abc import Awaitable
from typing import Never, TypeVar

from agent_framework import Executor, WorkflowContext, handler

from ..config.retrieval import FRESHNESS_WARNING_THRESHOLD
from ..embeddings.base import BaseEmbedder
from ..models.retrieval import RetrievedChunk, RetrieveResponse
from ..retrieval.freshness import FreshnessFilter
from ..retrieval.hybrid_search import HybridSearcher
from ..retrieval.reranker import Reranker
from ..utils.logging import get_logger
from ..vectordb.base import BaseVectorDB
from .state import RagState

logger = get_logger(__name__)

_T = TypeVar("_T")


async def _within(
    awaitable: Awaitable[_T], timeout_s: float | None, *, stage: str
) -> _T:
    """Await ``awaitable`` under a per-stage budget.

    ``timeout_s=None`` means no budget, which is the default for every stage — see
    ``config/retrieval.py``. In that case this adds a single ``await`` and nothing
    else, so behaviour is byte-identical to having no timeout machinery at all.

    On expiry this re-raises ``asyncio.TimeoutError`` after logging which stage
    blew its budget. Deciding what to *do* about it belongs to the caller, because
    the answer differs per stage: two of them degrade, two of them must fail.
    """
    if timeout_s is None:
        return await awaitable
    try:
        return await asyncio.wait_for(awaitable, timeout_s)
    except asyncio.TimeoutError:
        logger.warning("Stage exceeded its timeout", stage=stage, timeout_s=timeout_s)
        raise


class EncodeExecutor(Executor):
    """Step 1 — encode the query (dense + sparse).

    Calls ``embed_query`` and unpacks the dense/sparse vectors that
    ``HybridSearchExecutor`` needs. Also stamps ``started_at``, which
    ``AssembleResponseExecutor`` reads to report end-to-end latency.
    """

    def __init__(
        self, embedder: BaseEmbedder, timeout_s: float | None = None, id: str = "encode"
    ) -> None:
        super().__init__(id=id)
        self._embedder = embedder
        self._timeout_s = timeout_s

    @handler
    async def run(self, state: RagState, ctx: WorkflowContext[RagState]) -> None:
        state.started_at = time.perf_counter()

        # Propagates on timeout: with no query vector there is nothing to search,
        # and an empty result set would read to every caller as "no documents
        # matched" — a wrong answer dressed up as a valid one.
        embedding = await _within(
            self._embedder.embed_query(state.request.query), self._timeout_s, stage="encode"
        )
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
        self,
        searcher: HybridSearcher,
        pre_rerank_top_k: int,
        timeout_s: float | None = None,
        id: str = "hybrid_search",
    ) -> None:
        super().__init__(id=id)
        self._searcher = searcher
        self._pre_rerank_top_k = pre_rerank_top_k
        self._timeout_s = timeout_s

    @handler
    async def run(self, state: RagState, ctx: WorkflowContext[RagState]) -> None:
        # Propagates on timeout, for the same reason as encode: no candidates is
        # indistinguishable from "nothing matched" once it reaches the caller.
        state.candidates = await _within(
            self._searcher.search(
                dense_vector=state.dense_vector,
                sparse_indices=state.sparse_indices,
                sparse_values=state.sparse_values,
                filters=state.request.filters,
                pre_rerank_top_k=self._pre_rerank_top_k,
            ),
            self._timeout_s,
            stage="hybrid_search",
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

    def __init__(
        self, reranker: Reranker | None, timeout_s: float | None = None, id: str = "rerank"
    ) -> None:
        super().__init__(id=id)
        self._reranker = reranker
        self._timeout_s = timeout_s

    @handler
    async def run(self, state: RagState, ctx: WorkflowContext[RagState]) -> None:
        top_k = state.request.top_k
        if self._reranker and state.candidates:
            try:
                state.candidates = await _within(
                    self._reranker.rerank(
                        query=state.request.query, results=state.candidates, top_n=top_k
                    ),
                    self._timeout_s,
                    stage="rerank",
                )
            except asyncio.TimeoutError:
                # Degrade to RRF order rather than failing the request. This is the
                # same fallback reranker.py already takes when its model will not
                # load: the candidates are still relevant, just not reordered by the
                # cross-encoder. A slower answer in the right ballpark beats a 500.
                logger.warning("Rerank timed out; falling back to fused order", top_k=top_k)
                state.candidates = state.candidates[:top_k]
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

    def __init__(
        self, vectordb: BaseVectorDB, timeout_s: float | None = None, id: str = "parent_expand"
    ) -> None:
        super().__init__(id=id)
        self._vectordb = vectordb
        self._timeout_s = timeout_s

    @handler
    async def run(self, state: RagState, ctx: WorkflowContext[RagState]) -> None:
        if state.request.include_parent:
            parent_ids = [
                r.metadata.get("parent_chunk_id")
                for r in state.candidates
                if r.metadata.get("parent_chunk_id")
            ]
            try:
                parents = (
                    await _within(
                        self._vectordb.get_by_ids(list(set(filter(None, parent_ids)))),
                        self._timeout_s,
                        stage="parent_expand",
                    )
                    if parent_ids
                    else []
                )
            except asyncio.TimeoutError:
                # Degrade rather than fail: parent_text is optional on every chunk,
                # so the response is still well-formed and correctly ranked — the
                # generator just gets the child chunks without their surrounding
                # section.
                logger.warning("Parent expansion timed out; continuing without parents")
                parents = []
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
                < FRESHNESS_WARNING_THRESHOLD,
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
