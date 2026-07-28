"""Extractor abstract base class and shared dataclasses."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class ElementType(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    LIST_ITEM = "list_item"
    CAPTION = "caption"
    FIGURE = "figure"  # NEW — a picture/chart/diagram (multimodal extension)
    PAGE_IMAGE = "page_image"  # NEW — reserved for the future ColPali fallback path


@dataclass
class ExtractedElement:
    element_type: ElementType
    text: str
    level: int = 0  # Heading level (1, 2, 3) or 0 for non-headings
    page_number: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # NEW — raw cropped image bytes for FIGURE / low-confidence TABLE elements.
    # Cleared after enrichment writes them to the artifact store.
    image_bytes: bytes | None = None
    bbox: tuple[float, float, float, float] | None = None


@dataclass
class ExtractionResult:
    elements: list[ExtractedElement]
    raw_markdown: str  # Full document as markdown (for chunker)
    page_count: int
    file_path: str


class BaseExtractor(ABC):
    @abstractmethod
    async def extract(self, file_path: Path) -> ExtractionResult:
        """Extract structured content from a document file."""
        ...

    @abstractmethod
    def supports(self, file_path: Path) -> bool:
        """Return True if this extractor can handle the given file type."""
        ...
