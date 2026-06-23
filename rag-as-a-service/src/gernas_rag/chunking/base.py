"""Chunker abstract base class."""

import re
from abc import ABC, abstractmethod
from typing import Any

from ..extraction.base import ExtractionResult
from ..models.chunk import Chunk


class BaseChunker(ABC):
    @abstractmethod
    def chunk(self, extraction: ExtractionResult, base_metadata: dict[str, Any]) -> list[Chunk]:
        """Split an extraction result into chunks with full metadata."""
        ...

    @staticmethod
    def _extract_heading(text: str) -> str:
        """Return the first Markdown heading found in *text*, or an empty string."""
        m = re.search(r"^#{1,6}\s+(.+)", text, re.MULTILINE)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _extract_clause_ref(text: str, heading: str = "") -> str | None:
        """Return the first meaningful clause/section reference found in *heading*
        then *text* (full scan, not limited to first N chars).

        Patterns matched (in priority order):
          ``4.2.1``  ``Article 15``  ``Section 3``  ``Clause 4.2``  ``4.2``
        """
        patterns = [
            r"(\d+\.\d+\.\d+)",            # 4.2.1
            r"(Article\s+\d+(?:\.\d+)*)",  # Article 15
            r"(Section\s+\d+(?:\.\d+)*)",  # Section 3
            r"Clause\s+(\d+(?:\.\d+)*)",   # Clause 4.2
            r"(\d+\.\d+)",                 # 4.2
        ]
        for source in (heading, text):
            if not source:
                continue
            for pattern in patterns:
                m = re.search(pattern, source)
                if m:
                    return m.group(1)
        return None
