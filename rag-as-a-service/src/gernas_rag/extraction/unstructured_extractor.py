"""Fallback extractor — Unstructured.io (scanned PDFs / mixed formats)."""

import asyncio
from functools import partial
from pathlib import Path
from typing import Any

from ..config.chunking import ChunkingConfig
from ..utils.logging import get_logger
from .base import BaseExtractor, ElementType, ExtractedElement, ExtractionResult

logger = get_logger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc", ".html", ".txt", ".eml", ".pptx"}

_TYPE_MAP = {
    "Title": ElementType.HEADING,
    "Header": ElementType.HEADING,
    "NarrativeText": ElementType.PARAGRAPH,
    "Text": ElementType.PARAGRAPH,
    "Table": ElementType.TABLE,
    "ListItem": ElementType.LIST_ITEM,
    "FigureCaption": ElementType.CAPTION,
}


class UnstructuredExtractor(BaseExtractor):
    """Uses unstructured.io for OCR / hi-res partitioning of difficult documents.

    Partitioning is CPU-bound (and may invoke OCR), so it runs in a thread pool
    executor.
    """

    def __init__(self, config: ChunkingConfig | None = None) -> None:
        self._config = config or ChunkingConfig()

    def _sync_extract(self, file_path: Path) -> ExtractionResult:
        from unstructured.partition.auto import partition

        raw_elements = partition(
            filename=str(file_path),
            strategy=self._config.unstructured_strategy,
            infer_table_structure=self._config.infer_table_structure,
        )
        elements: list[ExtractedElement] = []
        markdown_parts: list[str] = []
        pages: set[int] = set()
        for el in raw_elements:
            category = el.__class__.__name__
            el_type = _TYPE_MAP.get(category, ElementType.PARAGRAPH)
            text = str(el).strip()
            if not text:
                continue
            page = getattr(getattr(el, "metadata", None), "page_number", None)
            if page is not None:
                pages.add(page)
            elements.append(
                ExtractedElement(
                    element_type=el_type,
                    text=text,
                    level=1 if el_type == ElementType.HEADING else 0,
                    page_number=page,
                    metadata={"category": category},
                )
            )
            markdown_parts.append(f"## {text}" if el_type == ElementType.HEADING else text)
        return ExtractionResult(
            elements=elements,
            raw_markdown="\n\n".join(markdown_parts),
            page_count=len(pages),
            file_path=str(file_path),
        )

    async def extract(self, file_path: Path) -> ExtractionResult:
        logger.info("Extracting document", path=str(file_path), extractor="unstructured")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(self._sync_extract, file_path))

    def supports(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in SUPPORTED_EXTENSIONS
