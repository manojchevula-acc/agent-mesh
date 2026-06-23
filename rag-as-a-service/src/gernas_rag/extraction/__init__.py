"""Document extraction abstraction."""

from .base import (
    BaseExtractor,
    ElementType,
    ExtractedElement,
    ExtractionResult,
)
from .factory import get_extractor

__all__ = [
    "BaseExtractor",
    "ElementType",
    "ExtractedElement",
    "ExtractionResult",
    "get_extractor",
]
