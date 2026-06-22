"""Primary extractor — IBM Docling (structure-preserving)."""

import asyncio
from functools import partial
from pathlib import Path
from typing import Any

from ..utils.logging import get_logger
from .base import BaseExtractor, ElementType, ExtractedElement, ExtractionResult

logger = get_logger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc", ".pptx", ".html", ".md"}


class DoclingExtractor(BaseExtractor):
    """Uses IBM Docling to extract structured content from PDFs and DOCX.

    Preserves heading hierarchy, tables, reading order. MIT licence — open source.
    Docling is CPU-bound, so conversion runs in a thread pool executor.
    """

    def __init__(self) -> None:
        # Two converters cached lazily: one with OCR enabled (for scanned PDFs),
        # one with OCR disabled (for digital PDFs with a text layer). Keyed by
        # the do_ocr flag so model loading is amortised across documents.
        self._converters: dict[bool, Any] = {}

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
            opts.images_scale = 1.0
            opts.generate_page_images = False

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

    def _sync_extract(self, file_path: Path) -> ExtractionResult:
        do_ocr = True
        if file_path.suffix.lower() == ".pdf":
            do_ocr = not self._pdf_has_text_layer(file_path)
        logger.info("Extraction mode", path=str(file_path), do_ocr=do_ocr)
        result = self._get_converter(do_ocr).convert(str(file_path))
        doc = result.document
        elements: list[ExtractedElement] = []
        for item, level in doc.iterate_items():
            label = str(getattr(item, "label", "paragraph")).lower()
            el_type = {
                "section_heading": ElementType.HEADING,
                "title": ElementType.HEADING,
                "paragraph": ElementType.PARAGRAPH,
                "text": ElementType.PARAGRAPH,
                "table": ElementType.TABLE,
                "list_item": ElementType.LIST_ITEM,
                "caption": ElementType.CAPTION,
            }.get(label, ElementType.PARAGRAPH)
            elements.append(
                ExtractedElement(
                    element_type=el_type,
                    text=item.text if hasattr(item, "text") else str(item),
                    level=level,
                    metadata={"label": label},
                )
            )
        return ExtractionResult(
            elements=elements,
            raw_markdown=doc.export_to_markdown(),
            page_count=getattr(doc, "num_pages", 0) or 0,
            file_path=str(file_path),
        )

    async def extract(self, file_path: Path) -> ExtractionResult:
        logger.info("Extracting document", path=str(file_path), extractor="docling")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(self._sync_extract, file_path))

    def supports(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in SUPPORTED_EXTENSIONS
