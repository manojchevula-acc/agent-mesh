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


@dataclass
class ExtractedElement:
    element_type: ElementType
    text: str
    level: int = 0  # Heading level (1, 2, 3) or 0 for non-headings
    page_number: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


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
