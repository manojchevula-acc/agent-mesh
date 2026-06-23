"""Utility extractor — PyMuPDF (fast raw text extraction)."""

import asyncio
from functools import partial
from pathlib import Path

from ..utils.logging import get_logger
from .base import BaseExtractor, ElementType, ExtractedElement, ExtractionResult

logger = get_logger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".xps", ".epub", ".cbz", ".fb2"}


class PyMuPDFExtractor(BaseExtractor):
    """Fast, no-frills text extraction via PyMuPDF (fitz).

    Produces one paragraph element per page. Used as a quick utility extractor
    when structure preservation is not required.
    """

    def _sync_extract(self, file_path: Path) -> ExtractionResult:
        import fitz  # PyMuPDF

        elements: list[ExtractedElement] = []
        markdown_parts: list[str] = []
        with fitz.open(str(file_path)) as doc:
            page_count = doc.page_count
            for page_index in range(page_count):
                page = doc.load_page(page_index)
                text = page.get_text("text").strip()
                if not text:
                    continue
                elements.append(
                    ExtractedElement(
                        element_type=ElementType.PARAGRAPH,
                        text=text,
                        level=0,
                        page_number=page_index + 1,
                    )
                )
                markdown_parts.append(text)
        return ExtractionResult(
            elements=elements,
            raw_markdown="\n\n".join(markdown_parts),
            page_count=page_count,
            file_path=str(file_path),
        )

    async def extract(self, file_path: Path) -> ExtractionResult:
        logger.info("Extracting document", path=str(file_path), extractor="pymupdf")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(self._sync_extract, file_path))

    def supports(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in SUPPORTED_EXTENSIONS
