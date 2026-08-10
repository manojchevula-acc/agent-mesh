"""Unit tests for ``IngestionPipeline._context_text`` — the text handed to the
VLM alongside a figure/table crop, so a title or caption sitting outside the
cropped bounding box is not simply lost.
"""

from __future__ import annotations

from gernas_rag.extraction.base import ElementType, ExtractedElement
from gernas_rag.ingestion.pipeline import _context_text


def _element(**metadata) -> ExtractedElement:
    return ExtractedElement(ElementType.FIGURE, "", metadata=metadata)


def test_combines_heading_and_docling_caption_when_both_present():
    el = _element(
        nearest_heading="3. Asset Quality",
        docling_caption="Figure 2.1 - LCR and NSFR trend, Q3-2022 to Q2-2024.",
    )
    assert _context_text(el) == (
        "3. Asset Quality. Caption: Figure 2.1 - LCR and NSFR trend, Q3-2022 to Q2-2024."
    )


def test_docling_caption_alone_is_used_verbatim():
    el = _element(nearest_heading="", docling_caption="Table 3.1 - Pricing floors.")
    assert _context_text(el) == "Table 3.1 - Pricing floors."


def test_falls_back_to_nearest_heading_when_no_caption_link_exists():
    # Most figures have no Docling caption link at all — the common case.
    el = _element(nearest_heading="2.4 Relationship Value", docling_caption="")
    assert _context_text(el) == "2.4 Relationship Value"


def test_neither_present_is_an_empty_string_not_none():
    el = _element(nearest_heading="", docling_caption="")
    assert _context_text(el) == ""


def test_missing_metadata_keys_degrade_the_same_as_empty_strings():
    # ExtractedElement.metadata is a plain dict; a key can simply be absent
    # rather than present-and-empty (e.g. non-FIGURE elements never set
    # docling_caption at all). Must not raise.
    el = ExtractedElement(ElementType.FIGURE, "", metadata={})
    assert _context_text(el) == ""
