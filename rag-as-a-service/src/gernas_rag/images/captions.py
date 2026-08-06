"""Resolve the textual context of an image.

Serves three consumers: the image-stub chunk (retrieval), the ``[IN]`` label that
precedes each image in the vision prompt, and the caption-only fallback when the
vision model is unavailable.

Priority order, highest reliability first:
  1. Structural caption from Docling (PictureItem / TableItem captions)
  2. Regex 'Figure N: ...' / 'Exhibit N: ...' near the anchor
  3. Surrounding prose window from the same page
  4. Nearest preceding heading
"""

import re
from dataclasses import dataclass

from ..config.multimodal import ImageExtractionConfig
from ..extraction.base import ElementType, ExtractionResult
from ..models.chunk import Chunk
from .base import RawImage

_CAPTION_RE = re.compile(
    r"^[ \t>*_#]*((?:figure|fig\.?|exhibit|chart|table|annex(?:ure)?|diagram)\s*"
    r"[0-9]*(?:\.[0-9]+)*\s*[:.\-–]?\s*.{3,200})$",
    re.IGNORECASE | re.MULTILINE,
)
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)


@dataclass
class ImageContext:
    caption: str = ""
    surrounding_text: str = ""
    nearest_heading: str = ""
    parent_chunk_id: str | None = None


class CaptionResolver:
    """Best-effort context resolution.

    KNOWN LIMITATION: association is page-level. A page with three figures gives
    all three the same surrounding window. Improving this needs bbox-to-text-block
    geometry matching (Docling exposes coordinates for both).
    """

    def __init__(self, config: ImageExtractionConfig) -> None:
        self._c = config

    def resolve(
        self,
        raw: RawImage,
        extraction: ExtractionResult,
        chunks: list[Chunk],
    ) -> ImageContext:
        page = raw.page_number
        window = self._page_window(extraction, page)
        caption = raw.caption or self._regex_caption(window)
        heading = self._nearest_heading(extraction, page, window)
        parent = self._best_chunk(chunks, page, caption or heading)
        return ImageContext(
            caption=caption,
            surrounding_text=window[: self._c.caption_window_chars],
            nearest_heading=heading,
            parent_chunk_id=parent.id if parent else None,
        )

    # ── Helpers ───────────────────────────────────────────────────────
    def _page_window(self, extraction: ExtractionResult, page: int | None) -> str:
        """Prose from the image's page, falling back to the document head."""
        if page is not None and extraction.elements:
            on_page = [
                el.text
                for el in extraction.elements
                if el.page_number == page and el.text.strip()
            ]
            if on_page:
                return "\n".join(on_page)[: self._c.caption_window_chars * 2]
        return extraction.raw_markdown[: self._c.caption_window_chars * 2]

    @staticmethod
    def _regex_caption(window: str) -> str:
        matches = _CAPTION_RE.findall(window)
        return matches[0].strip() if matches else ""

    def _nearest_heading(
        self, extraction: ExtractionResult, page: int | None, window: str
    ) -> str:
        if page is not None and extraction.elements:
            headings = [
                el.text
                for el in extraction.elements
                if el.element_type is ElementType.HEADING
                and el.page_number is not None
                and el.page_number <= page
                and el.text.strip()
            ]
            if headings:
                return headings[-1].strip()
        found = _HEADING_RE.findall(window)
        return found[-1].strip() if found else ""

    @staticmethod
    def _best_chunk(chunks: list[Chunk], page: int | None, hint: str) -> Chunk | None:
        """Pick the chunk this image most plausibly belongs to.

        Prefers a same-page child chunk; falls back to token overlap with the
        caption/heading hint, then to the first child chunk.
        """
        children = [c for c in chunks if not c.is_parent]
        if not children:
            return chunks[0] if chunks else None

        if page is not None:
            same_page = [c for c in children if c.metadata.source_page == page]
            if same_page:
                return same_page[0]

        hint_tokens = {t for t in re.findall(r"\w{4,}", hint.lower())}
        if hint_tokens:
            best, best_score = None, 0
            for c in children:
                score = len(hint_tokens & set(re.findall(r"\w{4,}", c.text.lower())))
                if score > best_score:
                    best, best_score = c, score
            if best is not None:
                return best
        return children[0]
