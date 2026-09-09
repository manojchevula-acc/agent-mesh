"""RetrievalPipeline — hybrid search + rerank + freshness + parent expansion.

The flow itself is orchestrated by a Microsoft Agent Framework workflow
(``gernas_rag.workflow``), which runs the stages as MAF Executors and emits
native ``workflow.run`` / ``executor.process`` OpenTelemetry spans. This class is
the public entry point and owns the components; the workflow only sequences them.

All seven call sites — the HTTP router, the MCP server, both eval runners, the
gold-set script and the tests — go through :meth:`retrieve` and are unaffected by
that orchestration choice.
"""

from ..config.embedding import EmbeddingProvider
from ..config.settings import Settings
from ..embeddings.base import BaseEmbedder
from ..models.retrieval import RetrieveRequest, RetrieveResponse
from ..utils.logging import get_logger
from ..vectordb.base import BaseVectorDB
from .freshness import FreshnessFilter
from .hybrid_search import HybridSearcher
from .reranker import Reranker

logger = get_logger(__name__)

# Canonical definition. Consumed by the workflow's AssembleResponseExecutor to set
# `freshness_warning` on every returned chunk, and mirrored (deliberately, with a
# note) by eval/stage4_generation/retrieval_reuse.py. Defined here rather than in
# the workflow package so it stays with the retrieval domain.
_FRESHNESS_WARNING_THRESHOLD = 0.7


class RetrievalPipeline:
    """Full retrieval pipeline:

    ``Query → Encode → Metadata filter → Dense ANN + Sparse BM25 → RRF merge →
    Cross-encoder rerank → Freshness penalty → Parent expand``

    Each stage is a MAF Executor; this class builds them once, wires them into an
    immutable workflow, and exposes the whole thing as one ``async`` call.
    """

    def __init__(self, settings: Settings, embedder: BaseEmbedder, vectordb: BaseVectorDB) -> None:
        # Imported here rather than at module scope: workflow.executors imports
        # _FRESHNESS_WARNING_THRESHOLD from this module, so a module-level import
        # in either direction would be circular.
        from ..workflow.build import build_retrieval_workflow

        self._settings = settings
        self._embedder = embedder
        self._vectordb = vectordb
        # These three stay private-by-convention attributes rather than being
        # inlined into the workflow: eval/stage3_retrieval/runner.py reaches into
        # all three directly to drive the production components while measuring
        # recall@k at a deeper rank than production returns.
        self._searcher = HybridSearcher(settings.retrieval, vectordb)
        self._reranker = (
            Reranker.from_embedding_config(settings.embedding)
            if settings.embedding.provider != EmbeddingProvider.SENTENCE_TRANSFORMER
            else None
        )
        self._freshness = FreshnessFilter(settings.retrieval)

        # Built once and reused. MAF workflows are immutable and concurrency-safe
        # because all per-request data travels on the RagState passed to run().
        self._workflow = build_retrieval_workflow(
            embedder=embedder,
            vectordb=vectordb,
            searcher=self._searcher,
            reranker=self._reranker,
            freshness=self._freshness,
            pre_rerank_top_k=settings.retrieval.pre_rerank_top_k,
        )

    async def retrieve(self, request: RetrieveRequest) -> RetrieveResponse:
        """Retrieve relevant chunks for a query.

        Runs the MAF workflow and unwraps its output. MAF has no typed return
        value: executors emit messages, and the ones yielded by executors named in
        ``output_from`` are collected on the run result. This method is the only
        place in the service that has to know that.

        Exceptions are deliberately **not** caught. ``workflow.run()`` propagates
        an executor's exception unchanged (verified against agent_framework
        1.17.0), so the router's own ``try/except`` still turns a failure into
        HTTP 500 and the MCP server's still turns it into ``{"error": ...}``.
        """
        from ..workflow.state import RagState

        events = await self._workflow.run(RagState(request=request))

        for output in reversed(events.get_outputs()):
            if isinstance(output, RagState) and output.response is not None:
                return output.response

        # Unreachable unless the graph was misbuilt (e.g. `assemble` dropped from
        # `output_from`). Fail loudly rather than returning an empty result set,
        # which would look like "no documents matched" to every caller.
        raise RuntimeError(
            "MAF retrieval workflow produced no RetrieveResponse output; "
            "check that AssembleResponseExecutor is listed in output_from"
        )
