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
):
    """Builds the immutable, reusable retrieval workflow.

    Called once from ``RetrievalPipeline.__init__``; the returned ``Workflow`` is
    safe to invoke concurrently because all per-request data travels on the
    ``RagState`` passed to ``run()``, never on the executors.
    """
    encode = EncodeExecutor(embedder, id="encode")
    search = HybridSearchExecutor(searcher, pre_rerank_top_k, id="hybrid_search")
    rerank = RerankExecutor(reranker, id="rerank")
    fresh = FreshnessExecutor(freshness, id="freshness")
    expand = ParentExpandExecutor(vectordb, id="parent_expand")
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
