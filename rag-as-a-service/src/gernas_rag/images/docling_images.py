"""Docling image extraction — slower, but yields real figure CAPTIONS and tables.

Requires a converter configured with ``generate_picture_images=True``, which the
existing text converter deliberately disables for memory reasons. We therefore
build a SEPARATE converter instance rather than mutating the shared one in
DoclingExtractor — the text path keeps its current memory profile.

This backend is also the only one that sees TABLES: Docling classifies them as
TableItem in ``doc.tables``, a collection distinct from ``doc.pictures``.
"""

import asyncio
import re
from functools import partial
from io import BytesIO
from pathlib import Path
from typing import Any

from ..config.multimodal import ImageExtractionConfig
from ..models.asset import ImageRole
from ..utils.logging import get_logger
from .base import BaseImageExtractor, RawImage
from .region_render import RegionRenderer

logger = get_logger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc", ".pptx", ".html", ".md"}

_CHART_RE = re.compile(r"chart|graph|plot|trend|histogram", re.IGNORECASE)
_DIAGRAM_RE = re.compile(r"diagram|flow|process|schematic|architecture", re.IGNORECASE)


class DoclingImageExtractor(BaseImageExtractor):
    def __init__(self, config: ImageExtractionConfig) -> None:
        self._c = config
        self._converter: Any | None = None
        self._renderer = RegionRenderer(config)

    def _get_converter(self) -> Any:
        if self._converter is None:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption

            opts = PdfPipelineOptions()
            opts.do_ocr = False  # The text path already handled OCR.
            opts.do_table_structure = True  # Needed to populate doc.tables.
            opts.generate_picture_images = True  # The switch that unlocks images.
            opts.generate_page_images = self._c.include_page_renders
            opts.images_scale = 2.0  # 2x render keeps small labels legible.

            self._converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
            )
        return self._converter

    async def extract_images(self, file_path: Path) -> list[RawImage]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(self._sync_extract, file_path))

    def _sync_extract(self, file_path: Path) -> list[RawImage]:
        result = self._get_converter().convert(str(file_path))
        doc = result.document
        out: list[RawImage] = []

        # ── Pictures ──────────────────────────────────────────────────
        for i, picture in enumerate(getattr(doc, "pictures", []) or []):
            if len(out) >= self._c.max_images_per_document:
                break
            pil = self._picture_image(picture, doc)
            if pil is None:
                continue
            caption = self._caption_of(picture, doc)
            out.append(
                RawImage(
                    data=_png_bytes(pil),
                    width=pil.width,
                    height=pil.height,
                    source_format="png",
                    page_number=_page_of(picture),
                    bbox=_bbox_of(picture),
                    index_on_page=i,
                    caption=caption,
                    role=_classify(caption),
                )
            )

        # ── Tables (D8) ───────────────────────────────────────────────
        # Rendered from bbox, not extracted: a PDF table is usually vector
        # strokes plus live text, so there is no image object to pull.
        table_crops = 0
        if self._c.extract_table_crops:
            for i, table in enumerate(getattr(doc, "tables", []) or []):
                if len(out) >= self._c.max_images_per_document:
                    break
                page_no, bbox = _page_of(table), _bbox_of(table)
                if page_no is None or bbox is None:
                    logger.warning(
                        "Table has no usable provenance; no crop",
                        index=i,
                        page=page_no,
                        has_bbox=bbox is not None,
                    )
                    continue
                rendered = self._renderer.render(
                    file_path,
                    page_no,
                    bbox,
                    role=ImageRole.TABLE_IMAGE,
                    bottom_left_origin=_is_bottom_left(table),
                )
                if rendered is None:
                    logger.warning(
                        "Table crop render failed", index=i, page=page_no, bbox=bbox
                    )
                    continue
                rendered.caption = self._caption_of(table, doc)
                rendered.index_on_page = i
                table_crops += 1
                out.append(rendered)

        n_tables = len(getattr(doc, "tables", []) or [])
        logger.info(
            "Docling image extraction complete",
            path=str(file_path),
            pictures=len(getattr(doc, "pictures", []) or []),
            tables=n_tables,
            table_crops=table_crops,  # must track `tables` when crops are enabled
            kept=len(out),
        )
        if self._c.extract_table_crops and n_tables and not table_crops:
            logger.warning(
                "Docling found tables but produced ZERO crops — check bbox "
                "coordinate origin handling",
                path=str(file_path),
                tables=n_tables,
            )
        return out

    @staticmethod
    def _picture_image(picture: Any, doc: Any) -> Any:
        """Docling's picture accessor has moved between 2.x releases."""
        for attempt in (
            lambda: picture.get_image(doc),
            lambda: picture.image.pil_image,
        ):
            try:
                image = attempt()
                if image is not None:
                    return image
            except Exception:  # noqa: BLE001, S110 - try the next accessor
                continue
        return None

    @staticmethod
    def _caption_of(item: Any, doc: Any) -> str:
        for attempt in (
            lambda: item.caption_text(doc),
            lambda: " ".join(c.text for c in (item.captions or []) if getattr(c, "text", "")),
        ):
            try:
                text = attempt()
                if text:
                    return str(text).strip()
            except Exception:  # noqa: BLE001, S110 - captions are best-effort
                continue
        return ""

    def supports(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in SUPPORTED_EXTENSIONS


def _classify(caption: str) -> ImageRole:
    if _CHART_RE.search(caption):
        return ImageRole.CHART
    if _DIAGRAM_RE.search(caption):
        return ImageRole.DIAGRAM
    return ImageRole.FIGURE if caption else ImageRole.UNKNOWN


def _page_of(item: Any) -> int | None:
    prov = getattr(item, "prov", None)
    if not prov:
        return None
    return getattr(prov[0], "page_no", None)


def _bbox_of(item: Any) -> tuple[float, float, float, float] | None:
    prov = getattr(item, "prov", None)
    if not prov:
        return None
    bbox = getattr(prov[0], "bbox", None)
    if bbox is None:
        return None
    try:
        return (float(bbox.l), float(bbox.t), float(bbox.r), float(bbox.b))
    except AttributeError:
        try:
            return tuple(float(v) for v in bbox)  # type: ignore[return-value]
        except Exception:  # noqa: BLE001
            return None


def _is_bottom_left(item: Any) -> bool:
    """Docling defaults to CoordOrigin.BOTTOMLEFT, which PyMuPDF cannot consume.

    Read it off the bbox rather than assuming: Docling has exposed both origins
    across versions, and guessing wrong flips every crop to the wrong half of
    the page.
    """
    prov = getattr(item, "prov", None)
    if not prov:
        return False
    origin = getattr(getattr(prov[0], "bbox", None), "coord_origin", None)
    return "BOTTOMLEFT" in str(origin).upper()


def _png_bytes(pil_image: Any) -> bytes:
    buf = BytesIO()
    pil_image.save(buf, format="PNG")
    return buf.getvalue()
