"""Rasterise an arbitrary page region.

Used for:
  * TABLES  - Docling TableItems are NOT in doc.pictures, and most PDF tables are
              vector strokes + live text, so there is no image object to extract.
              Rendering the bbox sidesteps both problems.
  * FIGURES - fallback when Docling detects a picture that PyMuPDF's get_images()
              cannot pull (vector-drawn charts).
"""

import asyncio
from functools import partial
from pathlib import Path

from ..config.multimodal import ImageExtractionConfig
from ..models.asset import ImageRole
from ..utils.logging import get_logger
from .base import RawImage

logger = get_logger(__name__)

_PDF_USER_SPACE_DPI = 72.0


class RegionRenderer:
    def __init__(self, config: ImageExtractionConfig) -> None:
        self._c = config

    def render(
        self,
        file_path: Path,
        page_number: int,
        bbox: tuple[float, float, float, float],
        role: ImageRole = ImageRole.TABLE_IMAGE,
        dpi: int | None = None,
    ) -> RawImage | None:
        """Render one page region. Returns None rather than raising on bad input."""
        try:
            import fitz  # pymupdf
        except ImportError:
            logger.warning("pymupdf unavailable; cannot render region")
            return None

        doc = None
        try:
            doc = fitz.open(str(file_path))
            if not 1 <= page_number <= doc.page_count:
                return None
            page = doc[page_number - 1]

            pad = self._c.table_crop_pad_pt
            # Padding keeps outer table rules from being shaved — a clipped
            # border reads as ambiguous to a vision model.
            clip = fitz.Rect(*bbox) + (-pad, -pad, pad, pad)
            clip = clip & page.rect  # never exceed the page
            if clip.is_empty or clip.width < 8 or clip.height < 8:
                return None

            zoom = (dpi or self._c.table_render_dpi) / _PDF_USER_SPACE_DPI
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip)
            return RawImage(
                data=pix.tobytes("png"),
                width=pix.width,
                height=pix.height,
                source_format="png",
                page_number=page_number,
                bbox=(clip.x0, clip.y0, clip.x1, clip.y1),
                role=role,
                metadata={"render": "region"},
            )
        except Exception as exc:  # noqa: BLE001 - rendering must never fail ingestion
            logger.warning(
                "Region render failed", path=str(file_path), page=page_number, error=str(exc)
            )
            return None
        finally:
            if doc is not None:
                doc.close()

    async def render_async(self, *args, **kwargs) -> RawImage | None:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(self.render, *args, **kwargs))
