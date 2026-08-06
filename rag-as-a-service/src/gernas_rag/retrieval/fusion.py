"""Pure functions over ranked lists.

No I/O — the tuning surface lives here and is exhaustively unit-testable.

Text scores (BGE-M3 cosine, post-cross-encoder) and image scores (SigLIP-2
cosine) are NOT comparable, so nothing here compares them numerically: fusion is
rank-based, and relevance is decided per-modality before fusion.
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from ..vectordb.base import SearchResult
from ..vectordb.image_store import ImageSearchResult


@dataclass
class FusedResult:
    kind: Literal["text", "image"]
    identifier: str
    score: float
    rank: int = 0


def gate_images(
    results: list[ImageSearchResult],
    floor: float,
    margin_ratio: float,
    final_k: int,
) -> list[ImageSearchResult]:
    """Two-stage relevance gate.

    An ANN always returns its top_k neighbours regardless of relevance, so
    without a gate a query about interest rates returns the 'least irrelevant'
    org chart at rank 1 with full confidence. The absolute floor removes globally
    weak matches; the relative margin removes the long tail behind a strong hit.
    """
    if not results:
        return []
    kept = [r for r in results if r.score >= floor]
    if not kept:
        return []
    s_max = max(r.score for r in kept)
    kept = [r for r in kept if r.score >= margin_ratio * s_max]
    kept.sort(key=lambda r: r.score, reverse=True)
    for i, r in enumerate(kept):
        r.rank = i
    return kept[:final_k]


def rrf_fuse(
    text_results: list[SearchResult],
    image_results: list[ImageSearchResult],
    k: int = 60,
    w_text: float = 1.0,
    w_image: float = 0.6,
) -> list[FusedResult]:
    """Rank-based fusion — the only sound way to combine two incomparable score
    scales. Identical in form to ``HybridSearcher._rrf_merge``, with per-modality
    weights added.
    """
    scored: dict[tuple[str, str], float] = defaultdict(float)
    for rank, r in enumerate(text_results):
        scored[("text", r.chunk_id)] += w_text / (k + rank)
    for rank, r in enumerate(image_results):
        scored[("image", r.asset_id)] += w_image / (k + rank)

    ordered = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)
    return [
        FusedResult(kind=kind, identifier=ident, score=score, rank=i)  # type: ignore[arg-type]
        for i, ((kind, ident), score) in enumerate(ordered)
    ]
