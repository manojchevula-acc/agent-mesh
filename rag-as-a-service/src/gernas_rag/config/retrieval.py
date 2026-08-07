"""Retrieval pipeline configuration."""

from pydantic import BaseModel


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
