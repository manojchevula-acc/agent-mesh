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


class RetrievalPipeline:
    """Full retrieval pipeline:

    ``Query → Encode → Metadata filter → Dense ANN + Sparse BM25 → RRF merge →
    Cross-encoder rerank → Freshness penalty → Parent expand``

    Each stage is a MAF Executor. The components are built once here; the workflow
    that sequences them is built **per request** — see :meth:`_build_workflow`.
    """

    def __init__(self, settings: Settings, embedder: BaseEmbedder, vectordb: BaseVectorDB) -> None:
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

    def _build_workflow(self):
        """Build a fresh Workflow for one request.

        **Not cached, and that is not an oversight.** A MAF ``Workflow`` refuses to
        run twice concurrently on the same instance::

            WorkflowException: Workflow is already running; concurrent runs are
            not allowed on the same instance.

        (``agent_framework/_workflows/_workflow.py:776`` — the guard is explicit and
        deliberate, keyed on a weakref to the in-flight run's response stream.)

        An earlier version of this class built the workflow once in ``__init__``
        and reused it, on the assumption that immutability plus per-request
        ``RagState`` made it concurrency-safe. It does not: two overlapping
        ``/api/v1/retrieve`` calls raised ``WorkflowException`` and the second
        request failed with HTTP 500. Every test and eval ran sequentially, so
        nothing caught it until ``test_concurrent_requests_do_not_cross_contaminate``.

        Building per request measures ~1.9 ms: six executor objects plus graph
        validation. The components they wrap are *not* rebuilt — the embedder,
        cross-encoder and vector DB client stay on this instance and load once — so
        this is object construction, not model loading. Against a retrieval whose
        encode + search + rerank runs in the hundreds of milliseconds, that is well
        under 1%. It is not free, and it is the price of correctness here.

        The import is function-local to break a package cycle: ``retrieval/__init__``
        imports this module, and ``workflow/build`` imports ``retrieval.freshness``,
        which re-enters that same ``__init__``.
        """
        from ..workflow.build import build_retrieval_workflow

        return build_retrieval_workflow(
            embedder=self._embedder,
            vectordb=self._vectordb,
            searcher=self._searcher,
            reranker=self._reranker,
            freshness=self._freshness,
            pre_rerank_top_k=self._settings.retrieval.pre_rerank_top_k,
            timeouts=self._settings.retrieval,
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

        events = await self._build_workflow().run(RagState(request=request))

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
