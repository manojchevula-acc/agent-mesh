"""Retrieval pipeline configuration."""

from pydantic import BaseModel


class RetrievalConfig(BaseModel):
    # ── Candidate pool ────────────────────────────────────────────────
    dense_top_k: int = 40  # Dense ANN candidates
    sparse_top_k: int = 40  # Sparse BM25 candidates
    rrf_k: int = 60  # RRF constant (60 is standard)
    pre_rerank_top_k: int = 20  # Candidates sent to reranker
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
