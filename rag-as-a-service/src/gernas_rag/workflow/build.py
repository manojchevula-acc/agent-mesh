"""Builds the retrieval Workflow.

Mirrors ``agent-mesh/src/mesh/workflow.py::build_mesh_workflow`` — the house
pattern for this monorepo.

The edge chain is deliberately linear::

    encode -> hybrid_search -> rerank -> freshness -> parent_expand -> assemble

There are no conditional edges. The two branches in the original flow
(``if self._reranker and candidates`` and ``if request.include_parent``) stay
inside their executors, so the emitted event sequence is identical for every
request regardless of which branch is taken.

Components are passed in rather than built here, so the workflow shares the
*same instances* ``RetrievalPipeline`` exposes as ``_searcher`` / ``_reranker`` /
``_freshness``. See MAF_MIGRATION_PLAN.md §4.2.
"""

from agent_framework import WorkflowBuilder

from ..config.retrieval import RetrievalConfig
from ..embeddings.base import BaseEmbedder
from ..retrieval.freshness import FreshnessFilter
from ..retrieval.hybrid_search import HybridSearcher
from ..retrieval.reranker import Reranker
from ..vectordb.base import BaseVectorDB
from .executors import (
    AssembleResponseExecutor,
    EncodeExecutor,
    FreshnessExecutor,
    HybridSearchExecutor,
    ParentExpandExecutor,
    RerankExecutor,
)


def build_retrieval_workflow(
    embedder: BaseEmbedder,
    vectordb: BaseVectorDB,
    searcher: HybridSearcher,
    reranker: Reranker | None,
    freshness: FreshnessFilter,
    pre_rerank_top_k: int,
    timeouts: RetrievalConfig | None = None,
):
    """Build one retrieval workflow.

    Called **per request** from ``RetrievalPipeline._build_workflow``, not once at
    startup. A MAF ``Workflow`` rejects a second concurrent ``run()`` on the same
    instance, so a cached workflow fails under any overlapping load — see that
    method's docstring for the full account.

    Only the graph is per-request. Every component passed in is owned by
    ``RetrievalPipeline`` and shared across all of them, so no model is reloaded.

    ``timeouts`` supplies the per-stage budgets; ``None`` leaves every stage
    unbounded, which is the default.
    """
    encode = EncodeExecutor(
        embedder, timeout_s=getattr(timeouts, "encode_timeout_s", None), id="encode"
    )
    search = HybridSearchExecutor(
        searcher,
        pre_rerank_top_k,
        timeout_s=getattr(timeouts, "search_timeout_s", None),
        id="hybrid_search",
    )
    rerank = RerankExecutor(
        reranker, timeout_s=getattr(timeouts, "rerank_timeout_s", None), id="rerank"
    )
    fresh = FreshnessExecutor(freshness, id="freshness")
    expand = ParentExpandExecutor(
        vectordb, timeout_s=getattr(timeouts, "parent_expand_timeout_s", None), id="parent_expand"
    )
    # No timeout: assemble is pure CPU over an in-memory list, so a hang there is a
    # bug a timeout would hide rather than contain.
    assemble = AssembleResponseExecutor(id="assemble")

    return (
        WorkflowBuilder(
            start_executor=encode,
            name="rag_retrieval_pipeline",
            description=(
                "encode -> hybrid_search -> rerank -> freshness -> "
                "parent_expand -> assemble"
            ),
            output_from=[assemble],
        )
        .add_edge(encode, search)
        .add_edge(search, rerank)
        .add_edge(rerank, fresh)
        .add_edge(fresh, expand)
        .add_edge(expand, assemble)
        .build()
    )
