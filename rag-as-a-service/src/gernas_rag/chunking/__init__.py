"""Text chunking strategies."""

from .base import BaseChunker
from .factory import get_chunker

__all__ = ["BaseChunker", "get_chunker"]
