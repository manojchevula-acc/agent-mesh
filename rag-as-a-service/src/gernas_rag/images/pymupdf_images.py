"""PyMuPDF image extraction — fast, gives page number and bbox.

The DEFAULT backend because it is decoupled from the Docling converter: it adds
zero memory pressure to the text pipeline, which matters given the std::bad_alloc
note in extraction/docling_extractor.py.

Limitation: get_images() finds only raster XObjects, so vector-drawn charts and
tables are invisible here — those come from the Docling backend plus
RegionRenderer.
"""

import asyncio
from functools import partial
from pathlib import Path

from ..config.multimodal import ImageExtractionConfig
from ..utils.logging import get_logger
from .base import BaseImageExtractor, RawImage

logger = get_logger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".xps", ".epub", ".cbz"}


class PyMuPDFImageExtractor(BaseImageExtractor):
    def __init__(self, config: ImageExtractionConfig) -> None:
        self._c = config

    async def extract_images(self, file_path: Path) -> list[RawImage]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(self._sync_extract, file_path))

    def _sync_extract(self, file_path: Path) -> list[RawImage]:
        import fitz  # pymupdf

        out: list[RawImage] = []
        doc = fitz.open(str(file_path))
        try:
            for page_index, page in enumerate(doc):
                per_page = 0
                for img_index, info in enumerate(page.get_images(full=True)):
                    if per_page >= self._c.max_images_per_page:
                        break
                    xref = info[0]
                    try:
                        pix = fitz.Pixmap(doc, xref)
                        # Normalise exotic colour spaces (CMYK, separation) to RGB.
                        if pix.n - pix.alpha >= 4:
                            pix = fitz.Pixmap(fitz.csRGB, pix)
                        data = pix.tobytes("png")
                        width, height = pix.width, pix.height
                        pix = None  # Release the C-side buffer promptly.
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "Skipping undecodable image", xref=xref, error=str(exc)
                        )
                        continue

                    # bbox lets us re-render a higher-quality crop later and lets
                    # a UI highlight the figure on the source page.
                    rects = page.get_image_rects(xref)
                    bbox = tuple(rects[0]) if rects else None

                    out.append(
                        RawImage(
                            data=data,
                            width=width,
                            height=height,
                            source_format="png",
                            page_number=page_index + 1,
                            bbox=bbox,
                            index_on_page=img_index,
                        )
                    )
                    per_page += 1
                    if len(out) >= self._c.max_images_per_document:
                        logger.info(
                            "Per-document image cap reached",
                            path=str(file_path),
                            cap=self._c.max_images_per_document,
                        )
                        return out
        finally:
            doc.close()

        logger.info("Image extraction complete", path=str(file_path), images=len(out))
        return out

    def supports(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in SUPPORTED_EXTENSIONS
