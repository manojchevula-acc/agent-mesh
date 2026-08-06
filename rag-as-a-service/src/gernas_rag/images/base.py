"""Image extraction contract."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models.asset import ImageRole


@dataclass
class RawImage:
    """An image as pulled from a document, BEFORE filtering / normalisation."""

    data: bytes
    width: int
    height: int
    source_format: str = "png"  # 'png' | 'jpeg' | 'raw' ...
    page_number: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    index_on_page: int = 0
    caption: str = ""  # Populated by structure-aware backends
    role: ImageRole = ImageRole.UNKNOWN
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_region_render(self) -> bool:
        """True when this came from RegionRenderer rather than an embedded object.

        Region renders are vouched for by a layout model, so they bypass the
        pixel-level blankness heuristic (sparse tables are legitimately
        low-variance).
        """
        return self.metadata.get("render") == "region"


class BaseImageExtractor(ABC):
    @abstractmethod
    async def extract_images(self, file_path: Path) -> list[RawImage]:
        """Extract candidate images from a document file."""
        ...

    @abstractmethod
    def supports(self, file_path: Path) -> bool:
        """Return True if this extractor can handle the given file type."""
        ...
