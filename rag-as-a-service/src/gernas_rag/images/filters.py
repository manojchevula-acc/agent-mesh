"""Reject images that are not worth indexing.

Without this, a 40-page policy PDF yields ~40 copies of the bank logo, ~40 header
rules and a handful of bullet glyphs — swamping the real figures and poisoning
retrieval.
"""

from dataclasses import dataclass
from io import BytesIO

from ..config.multimodal import ImageExtractionConfig
from ..models.asset import ImageRole
from ..utils.logging import get_logger
from .base import RawImage

logger = get_logger(__name__)


@dataclass
class FilterVerdict:
    keep: bool
    reason: str = ""


class ImageFilter:
    def __init__(self, config: ImageExtractionConfig) -> None:
        self._c = config

    def evaluate(self, raw: RawImage) -> FilterVerdict:
        c = self._c

        # A structurally-detected table region has already been vouched for by
        # the layout model, and sparse tables are legitimately low-variance —
        # the pixel heuristics below would wrongly discard them.
        if raw.role is ImageRole.TABLE_IMAGE or raw.is_region_render:
            if raw.width < 32 or raw.height < 32:
                return FilterVerdict(False, "degenerate_region")
            return FilterVerdict(True)

        if raw.width < c.min_width or raw.height < c.min_height:
            return FilterVerdict(False, "too_small")
        if raw.width * raw.height < c.min_area_px:
            return FilterVerdict(False, "area_below_threshold")

        short_side = max(1, min(raw.width, raw.height))
        if max(raw.width, raw.height) / short_side > c.max_aspect_ratio:
            return FilterVerdict(False, "extreme_aspect_ratio")  # rules, separators

        if self._is_near_uniform(raw):
            return FilterVerdict(False, "near_uniform")  # blank / solid fill

        return FilterVerdict(True)

    def _is_near_uniform(self, raw: RawImage) -> bool:
        """Std-dev of a downsampled greyscale copy. Cheap and effective."""
        try:
            import numpy as np
            from PIL import Image

            im = Image.open(BytesIO(raw.data)).convert("L").resize((32, 32))
            std = float(np.asarray(im, dtype="float32").std())
            return std < self._c.near_uniform_std_threshold
        except Exception as exc:  # noqa: BLE001 - a filter must never block ingestion
            logger.warning("Blankness check failed; keeping image", error=str(exc))
            return False
