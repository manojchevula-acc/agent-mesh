"""Primary extractor — IBM Docling (structure-preserving)."""

import asyncio
from functools import partial
from pathlib import Path

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
        # Single converter, created lazily on first use.
        # Pydantic 2.14a1 (required by Docling 2.x) has a bug where model_dump()
        # returns {} for inherited model fields inside spawned worker processes.
        # Docling's ThreadedPdfPipeline re-validates PdfFormatOption in its own
        # workers, hitting the bug.  Using DocumentConverter() with NO format_options
        # lets Docling construct its defaults internally, bypassing our code entirely
        # and avoiding the broken serialisation path.
        self._converter = None

    def _get_converter(self):
        if self._converter is None:
            from docling.document_converter import DocumentConverter
            self._converter = DocumentConverter()
        return self._converter

    def _sync_extract(self, file_path: Path) -> ExtractionResult:
        result = self._get_converter().convert(str(file_path))
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
