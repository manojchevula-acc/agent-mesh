"""RagState — the single message that flows through the retrieval workflow.

Every field corresponds to a stage boundary in the retrieval flow: the query
encoding, the candidate list as it is rebound by search → rerank → freshness, the
resolved parent sections, and the assembled response. See MAF_MIGRATION_PLAN.md
§9.3 for the field-by-field table.

Mutable and request-scoped. One instance is created per ``retrieve()`` call and
mutated in place as it passes down the edge chain, so no locking is needed — but
it must never be cached, shared between requests, or stored. MAF does not retain
a reference to it once ``workflow.run()`` returns (verified in Phase 0), so it is
garbage-collected with the request.

Deliberately carries no image bytes — only ``artifact_ref`` strings. Hydration
happens later, in ``generation/generator.py``, which is what keeps image bytes out
of the Redis cache. See MAF_MIGRATION_PLAN.md §11.3.
"""

from dataclasses import dataclass, field

from ..models.retrieval import RetrieveRequest, RetrieveResponse
from ..vectordb.base import SearchResult


@dataclass
class RagState:
    # --- Input (set by RetrievalPipeline before workflow.run) -------------
    request: RetrieveRequest

    # --- Timing -----------------------------------------------------------
    # Set by EncodeExecutor, read by AssembleResponseExecutor. Held on the state
    # rather than measured around workflow.run() so the reported latency covers
    # the whole retrieval span rather than only the assembly step.
    started_at: float = 0.0

    # --- Step 1: query encoding -------------------------------------------
    dense_vector: list[float] = field(default_factory=list)
    sparse_indices: list[int] = field(default_factory=list)
    sparse_values: list[float] = field(default_factory=list)

    # --- Steps 2-4: the candidate list ------------------------------------
    # Rebound in place by search -> rerank -> freshness; each stage replaces
    # the list with its own narrowed/reordered version.
    candidates: list[SearchResult] = field(default_factory=list)

    # --- Step 5: resolved parent sections ---------------------------------
    parent_map: dict[str, str] = field(default_factory=dict)

    # --- Terminal output --------------------------------------------------
    # Populated by AssembleResponseExecutor and collected via
    # ``events.get_outputs()``. None until the workflow completes.
    response: RetrieveResponse | None = None
