"""FreshnessFilter — applies a staleness penalty to search scores."""

from datetime import datetime, timezone

from ..config.retrieval import RetrievalConfig
from ..utils.logging import get_logger
from ..vectordb.base import SearchResult

logger = get_logger(__name__)

_DATE_FORMATS = ["%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%Y/%m/%d"]


class FreshnessFilter:
    """Reduces the score of stale chunks based on ``effective_date``.

    Chunks older than ``freshness_max_age_days`` are penalised linearly up to
    ``freshness_max_penalty``. The computed freshness score is written back into the
    result metadata so downstream code can raise freshness warnings.
    """

    def __init__(self, config: RetrievalConfig) -> None:
        self._config = config

    def apply(self, results: list[SearchResult]) -> list[SearchResult]:
        if not self._config.freshness_penalty_enabled:
            return results

        now = datetime.now(timezone.utc)
        out: list[SearchResult] = []
        for r in results:
            freshness = self._freshness_score(r.metadata.get("effective_date", ""), now)
            penalty = (1.0 - freshness) * self._config.freshness_max_penalty
            new_metadata = {**r.metadata, "freshness_score": freshness}
            out.append(
                SearchResult(
                    chunk_id=r.chunk_id,
                    text=r.text,
                    score=r.score * (1.0 - penalty),
                    metadata=new_metadata,
                    rank=r.rank,
                )
            )
        out.sort(key=lambda x: x.score, reverse=True)
        for i, r in enumerate(out):
            r.rank = i
        return out

    def _freshness_score(self, effective_date: str, now: datetime) -> float:
        if not effective_date:
            return 1.0
        effective = self._parse_date(effective_date)
        if effective is None:
            return 1.0
        age_days = (now - effective).days
        max_age = self._config.freshness_max_age_days
        if age_days <= max_age:
            return 1.0
        # Linearly decay from 1.0 at max_age to 0.0 at 2x max_age.
        decay = min((age_days - max_age) / max_age, 1.0)
        return max(1.0 - decay, 0.0)

    @staticmethod
    def _parse_date(value: str) -> datetime | None:
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(value.strip(), fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None
