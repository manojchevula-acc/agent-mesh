"""Primary extractor — IBM Docling (structure-preserving)."""

import asyncio
import io
from functools import partial
from pathlib import Path
from typing import Any

from ..config.enrichment import EnrichmentConfig
from ..utils.logging import get_logger
from .base import BaseExtractor, ElementType, ExtractedElement, ExtractionResult

logger = get_logger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc", ".pptx", ".html", ".md"}

_LABEL_MAP = {
    "section_header": ElementType.HEADING,
    "title": ElementType.HEADING,
    "paragraph": ElementType.PARAGRAPH,
    "text": ElementType.PARAGRAPH,
    "table": ElementType.TABLE,
    "list_item": ElementType.LIST_ITEM,
    "caption": ElementType.CAPTION,
    "picture": ElementType.FIGURE,
}


def _pil_to_png_bytes(pil_image: Any) -> bytes | None:
    """Serialise a PIL image to PNG bytes, or None if it can't be encoded."""
    try:
        buf = io.BytesIO()
        pil_image.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:  # noqa: BLE001 — image capture is best-effort.
        return None


class DoclingExtractor(BaseExtractor):
    """Uses IBM Docling to extract structured content from PDFs and DOCX.

    Preserves heading hierarchy, tables, reading order. MIT licence — open source.
    Docling is CPU-bound, so conversion runs in a thread pool executor.

    When an :class:`EnrichmentConfig` with ``enabled=True`` is supplied, Docling is
    asked to rasterise picture regions and cropped image bytes are attached to
    FIGURE / low-confidence TABLE elements for downstream vision captioning. With
    enrichment disabled (the default) behaviour is byte-for-byte unchanged.
    """

    def __init__(self, enrichment: EnrichmentConfig | None = None) -> None:
        # Two converters cached lazily: one with OCR enabled (for scanned PDFs),
        # one with OCR disabled (for digital PDFs with a text layer). Keyed by
        # the do_ocr flag so model loading is amortised across documents.
        self._converters: dict[bool, Any] = {}
        self._enrichment_enabled = bool(enrichment and enrichment.enabled)
        self._images_scale = enrichment.images_scale if enrichment else 1.0
        self._table_confidence_threshold = (
            enrichment.table_confidence_threshold if enrichment else 0.7
        )

    def _get_converter(self, do_ocr: bool) -> Any:
        if do_ocr not in self._converters:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption

            opts = PdfPipelineOptions()
            opts.do_ocr = do_ocr
            opts.do_table_structure = True
            # Caps page-rasterization memory when OCR is on, preventing the
            # std::bad_alloc that heavy pages trigger at the default scale.
            opts.images_scale = self._images_scale if self._enrichment_enabled else 1.0
            opts.generate_page_images = False
            if self._enrichment_enabled:
                # Ask Docling to keep cropped picture images so we can caption them.
                # Attribute set defensively — Docling versions differ on the exact
                # name; a mismatch degrades to "no image" rather than crashing.
                for attr in ("generate_picture_images", "generate_table_images"):
                    try:
                        setattr(opts, attr, True)
                    except Exception:  # noqa: BLE001
                        logger.debug("Docling option not available", option=attr)

            self._converters[do_ocr] = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=opts)
                }
            )
        return self._converters[do_ocr]

    def _pdf_has_text_layer(self, file_path: Path) -> bool:
        """Cheaply check whether a PDF carries an extractable text layer.

        Reads only the stored text objects (no rasterization), so a scanned
        PDF returns ~0 characters while a digital one returns plenty. Used to
        decide whether OCR is needed. Falls back to assuming no text layer
        (OCR on) if detection fails for any reason.
        """
        try:
            import pypdfium2 as pdfium

            pdf = pdfium.PdfDocument(str(file_path))
            try:
                page_count = len(pdf)
                total_chars = 0
                for page in pdf:
                    textpage = page.get_textpage()
                    total_chars += len(textpage.get_text_bounded().strip())
                    textpage.close()
                    page.close()
                avg_chars = total_chars / max(page_count, 1)
                # A genuine text layer averages well over ~50 chars/page;
                # scanned PDFs return close to zero.
                return avg_chars >= 50
            finally:
                pdf.close()
        except Exception as exc:  # noqa: BLE001 - detection must never block ingestion
            logger.warning(
                "Text-layer detection failed; defaulting to OCR",
                path=str(file_path),
                error=str(exc),
            )
            return False

    def _capture_image(self, item: Any, doc: Any) -> bytes | None:
        """Best-effort cropped-image capture for a picture/table item.

        Docling exposes the crop via ``item.get_image(doc)`` in recent versions;
        wrapped so an API difference degrades to no image (text-only fallback)
        rather than failing the whole extraction.
        """
        try:
            pil_image = item.get_image(doc)
        except Exception:  # noqa: BLE001
            return None
        if pil_image is None:
            return None
        return _pil_to_png_bytes(pil_image)

    def _caption_of(self, item: Any, doc: Any) -> str:
        """Resolve Docling's own figure/table caption link, when the layout model found one.

        ``item.get_image(doc)`` crops exactly the picture/table's own bounding
        box — it does not know a title or caption sits in a separate text block
        above or below it, so that text (and any date/label printed only there,
        never inside the plot area itself) is invisible to the VLM. Docling
        tracks the link anyway: a picture or table item carries ``.captions``,
        a list of references to the actual caption text elements the layout
        model associated with it. Resolving that costs nothing extra — the
        document is already fully parsed — and it is far more reliable than
        guessing from vertical position, because it is Docling's own layout
        judgement rather than a proximity heuristic re-derived from bboxes.
        """
        captions = getattr(item, "captions", None)
        if not captions:
            return ""
        texts = []
        for ref in captions:
            try:
                resolved = ref.resolve(doc)
                if getattr(resolved, "text", ""):
                    texts.append(resolved.text)
            except Exception:  # noqa: BLE001 — a bad ref must not fail extraction.
                continue
        return " ".join(texts)

    def _bbox_of(self, item: Any) -> tuple[float, float, float, float] | None:
        try:
            prov = getattr(item, "prov", None)
            if prov:
                bbox = prov[0].bbox
                return (float(bbox.l), float(bbox.t), float(bbox.r), float(bbox.b))
        except Exception:  # noqa: BLE001
            return None
        return None

    def _page_of(self, item: Any) -> int | None:
        try:
            prov = getattr(item, "prov", None)
            if prov:
                return int(prov[0].page_no)
        except Exception:  # noqa: BLE001
            return None
        return None

    def _sync_extract(self, file_path: Path) -> ExtractionResult:
        do_ocr = True
        if file_path.suffix.lower() == ".pdf":
            do_ocr = not self._pdf_has_text_layer(file_path)
        logger.info("Extraction mode", path=str(file_path), do_ocr=do_ocr)
        result = self._get_converter(do_ocr).convert(str(file_path))
        doc = result.document
        elements: list[ExtractedElement] = []
        current_heading = ""
        for item, level in doc.iterate_items():
            label = str(getattr(item, "label", "paragraph")).lower()
            el_type = _LABEL_MAP.get(label, ElementType.PARAGRAPH)

            if el_type == ElementType.HEADING and hasattr(item, "text"):
                current_heading = item.text

            image_bytes: bytes | None = None
            bbox: tuple[float, float, float, float] | None = None
            table_confidence: float | None = None
            metadata: dict[str, Any] = {"label": label, "nearest_heading": current_heading}

            if self._enrichment_enabled and el_type == ElementType.FIGURE:
                image_bytes = self._capture_image(item, doc)
                bbox = self._bbox_of(item)
                metadata["docling_caption"] = self._caption_of(item, doc)
            elif el_type == ElementType.TABLE:
                table_confidence = float(getattr(item, "confidence", 1.0) or 1.0)
                metadata["table_confidence"] = table_confidence
                # Only low-confidence tables need a vision pass; high-confidence
                # ones stay as Docling's markdown text (no image, no media chunk).
                if (
                    self._enrichment_enabled
                    and table_confidence < self._table_confidence_threshold
                ):
                    image_bytes = self._capture_image(item, doc)
                    bbox = self._bbox_of(item)
                    metadata["docling_caption"] = self._caption_of(item, doc)

            elements.append(
                ExtractedElement(
                    element_type=el_type,
                    # FIGURE/TABLE text must only ever come from the VLM caption
                    # written back during enrichment. Some Docling item types
                    # expose a `.text` attribute that isn't a caption at all but a
                    # diagnostic repr of the item (incl. embedded image bytes) —
                    # trusting it here would let that leak into the index as a
                    # "chunk" if the VLM call later fails and the fail-soft path
                    # reuses whatever text was already on the element.
                    text=(
                        ""
                        if el_type in (ElementType.FIGURE, ElementType.TABLE)
                        else (item.text if hasattr(item, "text") else "")
                    ),
                    level=level,
                    page_number=self._page_of(item),
                    metadata=metadata,
                    image_bytes=image_bytes,
                    bbox=bbox,
                )
            )
        return ExtractionResult(
            elements=elements,
            raw_markdown=doc.export_to_markdown(),
            page_count=doc.num_pages(),
            file_path=str(file_path),
        )

    async def extract(self, file_path: Path) -> ExtractionResult:
        logger.info("Extracting document", path=str(file_path), extractor="docling")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(self._sync_extract, file_path))

    def supports(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in SUPPORTED_EXTENSIONS
