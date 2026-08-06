"""Image rejection rules.

Without these, a 40-page policy PDF yields ~40 copies of the bank logo and ~40
header rules, swamping the real figures.
"""

from io import BytesIO

import pytest

from gernas_rag.config.multimodal import ImageExtractionConfig
from gernas_rag.images.base import RawImage
from gernas_rag.images.filters import ImageFilter
from gernas_rag.models.asset import ImageRole

PIL = pytest.importorskip("PIL.Image")


def _png(width: int, height: int, fill=(120, 30, 200), noise: bool = True) -> bytes:
    """A 'real figure' needs structure that SURVIVES the 32x32 downsample the
    blankness check applies — per-pixel noise averages away, large blocks don't.
    """
    image = PIL.new("RGB", (width, height), fill)
    if noise:
        pixels = image.load()
        block = max(1, min(width, height) // 8)
        for x in range(width):
            for y in range(height):
                if ((x // block) + (y // block)) % 2 == 0:
                    pixels[x, y] = (250, 250, 250)
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _raw(width: int, height: int, **kwargs) -> RawImage:
    fill = kwargs.pop("fill", (120, 30, 200))
    noise = kwargs.pop("noise", True)
    return RawImage(
        data=_png(width, height, fill, noise), width=width, height=height, **kwargs
    )


@pytest.fixture
def image_filter() -> ImageFilter:
    return ImageFilter(ImageExtractionConfig())


# ── Rejections ───────────────────────────────────────────────────────────
def test_tiny_image_rejected(image_filter):
    verdict = image_filter.evaluate(_raw(50, 50))
    assert not verdict.keep and verdict.reason == "too_small"


def test_below_area_threshold_rejected(image_filter):
    # 100x100 clears min_width/min_height but not min_area_px (20000).
    verdict = image_filter.evaluate(_raw(100, 100))
    assert not verdict.keep and verdict.reason == "area_below_threshold"


def test_header_rule_rejected_on_aspect_ratio(image_filter):
    verdict = image_filter.evaluate(_raw(2000, 20))
    assert not verdict.keep and verdict.reason in {
        "extreme_aspect_ratio",
        "too_small",
    }


def test_blank_block_rejected(image_filter):
    verdict = image_filter.evaluate(_raw(400, 300, fill=(255, 255, 255), noise=False))
    assert not verdict.keep and verdict.reason == "near_uniform"


# ── Acceptance ───────────────────────────────────────────────────────────
def test_real_figure_kept(image_filter):
    assert image_filter.evaluate(_raw(400, 300)).keep


# ── D8: table crops bypass the pixel heuristics ──────────────────────────
def test_sparse_table_crop_is_kept(image_filter):
    """A mostly-white table with thin rules would fail the blankness check —
    but the layout model already vouched for it."""
    raw = _raw(600, 400, fill=(252, 252, 252), noise=False, role=ImageRole.TABLE_IMAGE)
    verdict = image_filter.evaluate(raw)
    assert verdict.keep, f"table crop wrongly rejected as {verdict.reason}"


def test_region_render_bypasses_heuristics(image_filter):
    raw = _raw(600, 400, fill=(250, 250, 250), noise=False)
    raw.metadata["render"] = "region"
    assert image_filter.evaluate(raw).keep


def test_degenerate_region_still_rejected(image_filter):
    raw = _raw(20, 20, role=ImageRole.TABLE_IMAGE)
    verdict = image_filter.evaluate(raw)
    assert not verdict.keep and verdict.reason == "degenerate_region"


# ── Configurability ──────────────────────────────────────────────────────
def test_thresholds_are_configurable():
    lenient = ImageFilter(
        ImageExtractionConfig(min_width=8, min_height=8, min_area_px=64)
    )
    assert lenient.evaluate(_raw(50, 50)).keep
