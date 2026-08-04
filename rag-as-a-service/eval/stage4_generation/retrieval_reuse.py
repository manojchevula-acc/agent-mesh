"""Rebuild generation contexts from a cached stage3 retrieval ranking instead of a
fresh call to ``RetrievalPipeline``.

Retrieval (embed + hybrid search + cross-encoder rerank) is the CPU-bound part of
running the eval suite — stage 3 already pays it once per question. When you're
iterating on judge/RAGAS scoring logic rather than testing retrieval itself, redoing
that work in stage 4 for the same questions is pure waste. This module reconstructs
the ``RetrievedChunk`` objects generation needs from stage 3's cached ranking plus
``vectordb.get_by_ids`` (a couple of point lookups on the already-open client, no
model inference).

Deliberately *not* a ``ChunkIndex`` scroll: that opens its own, separate Qdrant
client, and against embedded/local storage (``qdrant_path`` set) a second client on
the same path fails outright — Qdrant's embedded engine allows exactly one open
handle per storage folder, even within the same process, even for two clients
created back-to-back. ``get_by_ids`` reuses the ``vectordb`` instance the run
already has open for live retrieval, so this never contends with itself.

What stays frozen vs. what's recomputed:
- **Order and score are frozen** at whatever they were when stage 3 ran — that
  ordering already reflects the freshness penalty *as of that run*, and there's no
  way to know if it would still hold today without re-running the pipeline. That's
  the whole tradeoff of reuse: it's an intentional snapshot, not a live number.
- **``freshness_warning`` is recomputed against now** — cheap, pure, and otherwise a
  reused run would silently understate staleness for documents that aged past the
  threshold since stage3 ran. It doesn't touch score or order, only the display flag.
- **``parent_text``/``artifact_ref``/``section_heading``/``effective_date`` come from
  a fresh point lookup**, since ``RankedHit`` (what stage 3 persists) never carried
  them — generation needs them (parent-chunk expansion, image hydration) regardless
  of whether retrieval itself was reused.

A chunk that no longer resolves (deleted/re-ingested since stage 3 ran) is silently
dropped rather than fabricated — better to generate from a slightly shorter context
window than from an ``artifact_ref`` that resolves to nothing.
"""

from __future__ import annotations

from gernas_rag.config.settings import Settings
from gernas_rag.models.retrieval import RetrievedChunk
from gernas_rag.retrieval.freshness import FreshnessFilter
from gernas_rag.vectordb.base import BaseVectorDB, SearchResult

from ..core.models import RankedHit

# Mirrors RetrievalPipeline's own threshold (src/gernas_rag/retrieval/pipeline.py) —
# duplicated rather than imported since that constant is module-private there.
_FRESHNESS_WARNING_THRESHOLD = 0.7

# Fields that change *what the ranking would be*, not just how deep it's read —
# a mismatch here means the cached order can't be trusted to represent current
# config. final_top_k/rank_depth are deliberately excluded: stage 4 truncates the
# cached ranking to its own top_k regardless of what stage 3 recorded.
_CONFIG_FIELDS = (
    "collection",
    "dense_top_k",
    "sparse_top_k",
    "rrf_k",
    "pre_rerank_top_k",
    "embedding_model",
    "freshness_penalty_enabled",
)


def config_mismatches(stage3_config: dict, settings: Settings) -> list[str]:
    """Compare a cached stage3 run's recorded config against current settings.

    Returns a human-readable mismatch per differing field; empty means the cached
    ranking is safe to reuse as-is.
    """
    current = {
        "collection": settings.vectordb.collection_name,
        "dense_top_k": settings.retrieval.dense_top_k,
        "sparse_top_k": settings.retrieval.sparse_top_k,
        "rrf_k": settings.retrieval.rrf_k,
        "pre_rerank_top_k": settings.retrieval.pre_rerank_top_k,
        "embedding_model": settings.embedding.model_name,
        "freshness_penalty_enabled": settings.retrieval.freshness_penalty_enabled,
    }
    mismatches = []
    for field in _CONFIG_FIELDS:
        recorded = stage3_config.get(field)
        live = current[field]
        if recorded != live:
            mismatches.append(f"{field}: stage3 run has {recorded!r}, current config has {live!r}")
    return mismatches


def _freshness_warning(effective_date: str, freshness: FreshnessFilter) -> bool:
    """Recompute staleness against *now*, without disturbing frozen score/order.

    Runs the real ``FreshnessFilter`` on a singleton list — reordering is a no-op
    on one item, so this reuses the exact production staleness math via its public
    ``apply()`` rather than reaching into a private method.
    """
    probe = SearchResult(chunk_id="", text="", score=1.0, metadata={"effective_date": effective_date}, rank=0)
    (scored,) = freshness.apply([probe])
    return scored.metadata.get("freshness_score", 1.0) < _FRESHNESS_WARNING_THRESHOLD


async def build_reused_chunks(
    hits: list[RankedHit],
    top_k: int,
    vectordb: BaseVectorDB,
    settings: Settings,
) -> list[RetrievedChunk]:
    """Rebuild the top-``top_k`` window of a cached stage3 ranking as ``RetrievedChunk``s.

    Chunks that no longer resolve (re-ingested/deleted since stage3 ran) are skipped,
    so the window can come back shorter than ``top_k`` — treated the same as a
    live retrieval call returning fewer than requested.
    """
    window = hits[:top_k]
    primary = {c.id: c for c in await vectordb.get_by_ids([h.chunk_id for h in window])}

    parent_ids = {c.metadata.parent_chunk_id for c in primary.values() if c.metadata.parent_chunk_id}
    parents = {c.id: c for c in await vectordb.get_by_ids(list(parent_ids))} if parent_ids else {}

    freshness = FreshnessFilter(settings.retrieval)
    chunks: list[RetrievedChunk] = []
    for hit in window:
        chunk = primary.get(hit.chunk_id)
        if chunk is None:
            continue
        meta = chunk.metadata
        parent = parents.get(meta.parent_chunk_id) if meta.parent_chunk_id else None
        chunks.append(
            RetrievedChunk(
                text=chunk.text,
                source=meta.document_name,
                section_heading=meta.section_heading,
                clause_reference=meta.clause_reference,
                score=hit.score,  # frozen from stage3 — see module docstring
                effective_date=meta.effective_date,
                freshness_warning=(
                    _freshness_warning(meta.effective_date, freshness)
                    if settings.retrieval.freshness_penalty_enabled
                    else False
                ),
                parent_text=parent.text if parent else None,
                modality=meta.modality.value if hasattr(meta.modality, "value") else meta.modality,
                artifact_ref=meta.artifact_ref,
            )
        )
    return chunks
