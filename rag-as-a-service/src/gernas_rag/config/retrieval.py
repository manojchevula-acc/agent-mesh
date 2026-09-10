"""Retrieval pipeline configuration."""

from pydantic import BaseModel

# Below this freshness score a chunk is returned with ``freshness_warning=True``.
#
# Lives here rather than in ``retrieval/pipeline.py`` so that both the pipeline and
# ``workflow/executors.py`` can import it without an import cycle — config depends on
# nothing in either package. ``eval/stage4_generation/retrieval_reuse.py`` imports it
# too, so the served path and the eval path cannot drift on the value.
#
# Deliberately a module constant rather than a ``RetrievalConfig`` field: the
# executors that read it are constructed with components, not settings, and this
# threshold has never been tuned per-deployment. Promote it to a field if that
# changes.
FRESHNESS_WARNING_THRESHOLD = 0.7


class RetrievalConfig(BaseModel):
    # ── Candidate pool ────────────────────────────────────────────────
    dense_top_k: int = 40  # Dense ANN candidates
    sparse_top_k: int = 40  # Sparse BM25 candidates
    rrf_k: int = 60  # RRF constant (60 is standard)
    # Candidates sent to the reranker. Raised 20 -> 40: the cross-encoder can
    # only reorder what RRF hands it, so this is a hard ceiling on recall that
    # no amount of reranking quality can recover from. Figure chunks lose the
    # RRF stage disproportionately (their captions are markdown scaffolding, so
    # they rank low in both retrievers before the cross-encoder ever sees them),
    # and every question that missed outright in eval stage 3 needed a figure.
    # Widening the window costs one extra cross-encoder batch per query and is
    # the cheaper half of the fix; the other half is giving captions prose to
    # match on (see _TRANSCRIBE_PROMPT in enrichment/vision_llm_enricher.py).
    pre_rerank_top_k: int = 40
    final_top_k: int = 5  # Final results returned

    # ── Freshness ─────────────────────────────────────────────────────
    freshness_penalty_enabled: bool = True
    freshness_max_age_days: int = 180  # Penalise chunks older than 6 months
    freshness_max_penalty: float = 0.3  # Max 30% score reduction

    # ── Parent expansion ──────────────────────────────────────────────
    include_parent_chunks: bool = True

    # ── Hybrid weights (for DBs that need explicit weights) ───────────
    dense_weight: float = 0.6  # Weight for dense score in fusion
    sparse_weight: float = 0.4  # Weight for sparse score in fusion

    # ── Per-stage timeouts ────────────────────────────────────────────
    # Budget in seconds for one workflow executor. ``None`` disables the timeout
    # for that stage, which is the default for all four: this service's own eval
    # runs take 90-150 s per query on CPU, so any value shipped as a default would
    # be wrong for somebody. Set them per deployment once you have measured p99.
    #
    # Behaviour on expiry differs by stage, and follows the degradation the
    # components already implement rather than inventing a new policy:
    #
    #   rerank        -> truncate to top_k and continue. The reranker already
    #                    fails soft this way when its model will not load.
    #   parent_expand -> continue with no parents. ``parent_text`` is optional on
    #                    every chunk, so the response stays valid.
    #   encode        -> propagate. Without a query vector there is nothing to
    #                    search; returning empty would read as "no documents
    #                    matched", which is a wrong answer rather than an error.
    #   hybrid_search -> propagate, for the same reason.
    #
    # ``assemble`` has no timeout: it is pure CPU over an in-memory list, so a
    # hang there is a bug that a timeout would mask rather than mitigate.
    encode_timeout_s: float | None = None
    search_timeout_s: float | None = None
    rerank_timeout_s: float | None = None
    parent_expand_timeout_s: float | None = None
