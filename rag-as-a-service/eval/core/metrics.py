"""Pure ranking and agreement metrics.

Every function takes plain lists/sets/dicts and returns plain numbers, with no
knowledge of the pipeline — which is what makes them unit-testable (see
``tests/unit/test_eval_metrics.py``) and safe to reuse across stages.

Convention: a metric that has *no data* to work with returns ``None``, never
0.0. A question with no relevant chunks judged would otherwise contribute a
perfect 0 to the "misses" column and quietly drag every average down.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Iterable, Sequence


def mean(values: Iterable[float | None]) -> float | None:
    """Arithmetic mean, ignoring ``None`` entries. ``None`` if nothing is left."""
    present = [v for v in values if v is not None]
    if not present:
        return None
    return sum(present) / len(present)


def percentile(values: Sequence[float], p: float) -> float | None:
    """Linear-interpolated percentile (``p`` in 0..100)."""
    present = sorted(v for v in values if v is not None)
    if not present:
        return None
    if len(present) == 1:
        return present[0]
    position = (len(present) - 1) * (p / 100.0)
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return present[int(position)]
    return present[low] + (present[high] - present[low]) * (position - low)


# ── Binary relevance ──────────────────────────────────────────────────
def recall_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float | None:
    """Share of relevant items appearing in the top ``k``."""
    if not relevant:
        return None
    return len(set(ranked[:k]) & relevant) / len(relevant)


def precision_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float | None:
    """Share of the top ``k`` that is relevant."""
    window = ranked[:k]
    if not window:
        return None
    return len(set(window) & relevant) / len(window)


def hit_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float | None:
    """1.0 when at least one relevant item is in the top ``k``."""
    if not relevant:
        return None
    return 1.0 if set(ranked[:k]) & relevant else 0.0


def first_relevant_rank(ranked: Sequence[str], relevant: set[str]) -> int | None:
    """1-based rank of the first relevant item, or ``None`` if never retrieved."""
    for i, item in enumerate(ranked, start=1):
        if item in relevant:
            return i
    return None


def reciprocal_rank(ranked: Sequence[str], relevant: set[str]) -> float | None:
    """1 / rank of the first relevant item; 0.0 when relevant items exist but miss."""
    if not relevant:
        return None
    rank = first_relevant_rank(ranked, relevant)
    return 1.0 / rank if rank else 0.0


# ── Graded relevance ──────────────────────────────────────────────────
def dcg(gains: Sequence[float]) -> float:
    """Discounted cumulative gain with the standard log2(rank+1) discount."""
    return sum(g / math.log2(i + 1) for i, g in enumerate(gains, start=1))


def ndcg_at_k(ranked: Sequence[str], grades: dict[str, int], k: int) -> float | None:
    """nDCG@k using exponential gain (2^grade - 1).

    This is the ordering metric: unlike recall it changes when a correct chunk
    moves from rank 1 to rank 5, which is exactly the regression a reranker
    introduces when it demotes caption text.
    """
    positive = {cid: g for cid, g in grades.items() if g > 0}
    if not positive:
        return None
    actual = dcg([2 ** positive.get(cid, 0) - 1 for cid in ranked[:k]])
    ideal = dcg([2**g - 1 for g in sorted(positive.values(), reverse=True)[:k]])
    if ideal == 0:
        return None
    return actual / ideal


# ── Ranking stability ─────────────────────────────────────────────────
def rank_of(ranked: Sequence[str], item: str) -> int | None:
    """1-based position of ``item``, or ``None`` when absent."""
    for i, candidate in enumerate(ranked, start=1):
        if candidate == item:
            return i
    return None


def rank_movements(
    before: Sequence[str], after: Sequence[str], tracked: set[str]
) -> dict[str, tuple[int | None, int | None]]:
    """Rank of each tracked item before and after a re-ordering stage."""
    return {item: (rank_of(before, item), rank_of(after, item)) for item in tracked}


def count_demotions(
    before: Sequence[str], after: Sequence[str], tracked: set[str]
) -> tuple[int, int]:
    """(demoted, dropped) counts for tracked items across a re-ordering stage.

    ``demoted`` = present in both but ranked worse afterwards.
    ``dropped``  = present before, absent after (fell out of the window).
    A reranker that improves the average while demoting known-relevant chunks is
    a real and easy-to-miss regression; this is what surfaces it.
    """
    demoted = dropped = 0
    for item, (before_rank, after_rank) in rank_movements(before, after, tracked).items():
        if before_rank is None:
            continue
        if after_rank is None:
            dropped += 1
        elif after_rank > before_rank:
            demoted += 1
    return demoted, dropped


# ── Set comparison ────────────────────────────────────────────────────
def prf(predicted: set[str], actual: set[str]) -> tuple[float | None, float | None, float | None]:
    """Precision, recall and F1 for two sets. ``None`` where undefined."""
    tp = len(predicted & actual)
    precision = tp / len(predicted) if predicted else None
    recall = tp / len(actual) if actual else None
    if precision is None or recall is None or (precision + recall) == 0:
        return precision, recall, None
    return precision, recall, 2 * precision * recall / (precision + recall)


# ── Agreement ─────────────────────────────────────────────────────────
def cohens_kappa(a: Sequence[str], b: Sequence[str]) -> float | None:
    """Cohen's kappa between two label sequences of equal length.

    Used to calibrate the LLM judge against human labels. A high pass rate from
    an uncalibrated judge is not evidence of quality; kappa is what turns the
    judge's verdicts into something you can cite.
    """
    if len(a) != len(b) or not a:
        return None
    n = len(a)
    observed = sum(1 for x, y in zip(a, b) if x == y) / n
    count_a, count_b = Counter(a), Counter(b)
    expected = sum((count_a[label] / n) * (count_b[label] / n) for label in set(a) | set(b))
    if expected == 1.0:
        # Both raters used a single identical label — kappa is undefined here.
        return None
    return (observed - expected) / (1 - expected)


# ── Distributions ─────────────────────────────────────────────────────
def histogram(values: Iterable[int | None], buckets: Sequence[int]) -> dict[str, int]:
    """Bucket 1-based ranks into ``<=b`` bins plus a ``>max`` and a ``miss`` bin.

    An average rank hides the tail; this is the view that tells you whether a
    poor MRR is "everything is at rank 3" or "most at rank 1, four total misses".
    """
    out: dict[str, int] = {f"<={b}": 0 for b in buckets}
    out[f">{buckets[-1]}"] = 0
    out["miss"] = 0
    for value in values:
        if value is None:
            out["miss"] += 1
            continue
        for bucket in buckets:
            if value <= bucket:
                out[f"<={bucket}"] += 1
                break
        else:
            out[f">{buckets[-1]}"] += 1
    return out
