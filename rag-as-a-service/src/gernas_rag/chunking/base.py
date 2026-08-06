"""Chunker abstract base class and shared table-protection helpers."""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from ..extraction.base import ExtractionResult
from ..models.chunk import Chunk

# ── Table protection (D8) ────────────────────────────────────────────────
# A markdown pipe-table: a header row, a delimiter row, then >=1 body rows.
# The delimiter row is the real signal — requiring it means prose that merely
# contains pipe characters is never mistaken for a table.
_TABLE_BLOCK = re.compile(
    r"(?:^[ \t]*\|.*\|[ \t]*\n)"  # header row
    r"(?:^[ \t]*\|[\s:\-|]+\|[ \t]*\n)"  # delimiter row
    r"(?:^[ \t]*\|.*\|[ \t]*\n?)+",  # >=1 body rows
    re.MULTILINE,
)

_PLACEHOLDER_RE = re.compile(r"\[\[TABLE_(\d+)\]\]")

# "Table 3: Pricing tiers", "Exhibit 2 - Limits", "Schedule A. Fees"
_TABLE_CAPTION_RE = re.compile(
    r"^[ \t>*_#]*((?:table|exhibit|annex(?:ure)?|schedule|figure)\s*"
    r"[0-9]*(?:\.[0-9]+)*\s*[:.\-\u2013]?\s*.{0,180})$",
    re.IGNORECASE | re.MULTILINE,
)

_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)

# A preceding-text window at or above its budget was cut mid-line, so its first
# line is a fragment rather than real content.
_CAPTION_WINDOW_TRUNCATED_AT = 100


@dataclass
class TableBlock:
    """A markdown table lifted out of the document before prose splitting."""

    key: str  # e.g. "TABLE_0"
    markdown: str
    preceding_text: str  # Window of prose immediately above the table
    caption: str = ""

    @property
    def placeholder(self) -> str:
        return f"[[{self.key}]]"


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

    # ── Table protection helpers (D8) ─────────────────────────────────
    @staticmethod
    def _mask_tables(text: str, caption_window: int = 200) -> tuple[str, list[TableBlock]]:
        """Replace every markdown table with an opaque placeholder.

        Returns the masked text plus the lifted tables. Masking BEFORE any
        splitting is what makes tables atomic: the character splitter never
        sees a table, so it cannot break one between rows.
        """
        blocks: list[TableBlock] = []

        def _sub(match: re.Match) -> str:
            key = f"TABLE_{len(blocks)}"
            start = match.start()
            blocks.append(
                TableBlock(
                    key=key,
                    markdown=match.group(0).strip(),
                    preceding_text=text[max(0, start - caption_window) : start],
                )
            )
            return f"\n\n[[{key}]]\n\n"

        masked = _TABLE_BLOCK.sub(_sub, text)
        for block in blocks:
            block.caption = BaseChunker._table_caption(block)
        return masked, blocks

    @staticmethod
    def _table_caption(block: TableBlock) -> str:
        """Best-effort title for a table, from the prose immediately above it.

        Table ROWS are excluded from every strategy: a table that Docling splits
        across a page boundary emits two adjacent pipe-blocks, and without this
        filter the second block's "caption" becomes the last row of the first.
        Any line CONTAINING a pipe is dropped, not just one starting with it —
        the fixed-size window often truncates mid-row, leaving a fragment with
        no leading pipe. Genuine captions do not contain pipe characters.
        """
        lines = block.preceding_text.splitlines()
        if len(block.preceding_text) >= _CAPTION_WINDOW_TRUNCATED_AT and lines:
            lines = lines[1:]  # first line is a truncation artefact
        window = "\n".join(ln for ln in lines if "|" not in ln)
        matches = _TABLE_CAPTION_RE.findall(window)
        if matches:
            return matches[-1].strip()
        headings = _HEADING_RE.findall(window)
        if headings:
            return headings[-1].strip()
        # Fall back to the last non-empty line — often an introducing sentence.
        lines = [ln.strip() for ln in window.splitlines() if ln.strip()]
        return lines[-1][:180] if lines else ""

    @staticmethod
    def _placeholders_in(text: str) -> list[str]:
        return [f"TABLE_{n}" for n in _PLACEHOLDER_RE.findall(text)]

    @staticmethod
    def _strip_placeholders(text: str) -> str:
        """Remove table placeholders and collapse the blank lines they leave."""
        cleaned = _PLACEHOLDER_RE.sub("", text)
        return re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    @staticmethod
    def _split_table_by_rows(table_md: str, budget_chars: int) -> list[str]:
        """Split an oversized table by rows, REPEATING the header in every part.

        This is the entire point of the fix: a part containing rows without their
        header is worse than useless — it invites a confident wrong answer.
        """
        table_md = table_md.strip()
        lines = [ln for ln in table_md.split("\n") if ln.strip()]
        if len(lines) < 3 or len(table_md) <= budget_chars:
            return [table_md]

        header, delim, rows = lines[0], lines[1], lines[2:]
        prefix = f"{header}\n{delim}\n"
        parts: list[str] = []
        current: list[str] = []
        for row in rows:
            candidate = prefix + "\n".join(current + [row])
            if current and len(candidate) > budget_chars:
                parts.append(prefix + "\n".join(current))
                current = [row]
            else:
                current.append(row)
        if current:
            parts.append(prefix + "\n".join(current))
        return parts

    @staticmethod
    def _count_table_rows(table_md: str) -> int:
        """Body rows, excluding the header and delimiter rows."""
        lines = [ln for ln in table_md.strip().split("\n") if ln.strip()]
        return max(0, len(lines) - 2)
