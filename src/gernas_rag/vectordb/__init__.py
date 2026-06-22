"""Vector database abstraction."""

from .base import BaseVectorDB, SearchResult
from .factory import get_vectordb

__all__ = ["BaseVectorDB", "SearchResult", "get_vectordb"]
