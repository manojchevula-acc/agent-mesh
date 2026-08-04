"""Unit tests for the extraction module (factory + interfaces)."""

import types
from pathlib import Path

from gernas_rag.config.chunking import ChunkingConfig, ExtractionStrategy
from gernas_rag.extraction.base import ElementType, ExtractedElement, ExtractionResult
from gernas_rag.extraction.docling_extractor import _LABEL_MAP, DoclingExtractor
from gernas_rag.extraction.factory import get_extractor


def test_extraction_result_dataclass():
    el = ExtractedElement(ElementType.HEADING, "Title", level=1)
    result = ExtractionResult(elements=[el], raw_markdown="# Title", page_count=1, file_path="x.pdf")
    assert result.elements[0].element_type == ElementType.HEADING
    assert result.page_count == 1


def test_factory_pymupdf_strategy():
    config = ChunkingConfig(extraction_strategy=ExtractionStrategy.PYMUPDF)
    extractor = get_extractor(config, Path("doc.pdf"))
    assert extractor.supports(Path("doc.pdf"))
    assert not extractor.supports(Path("doc.csv"))


def test_factory_auto_selects_docling_for_pdf():
    config = ChunkingConfig(extraction_strategy=ExtractionStrategy.AUTO)
    extractor = get_extractor(config, Path("doc.pdf"))
    # Docling supports pdf; AUTO should return a docling extractor for it.
    assert extractor.supports(Path("doc.pdf"))


def test_element_type_values():
    assert ElementType.TABLE.value == "table"
    assert ElementType.LIST_ITEM.value == "list_item"


def test_label_map_recognizes_docling_section_header():
    # Docling's real DocItemLabel enum value is "section_header" (not
    # "section_heading") — a mismatch here silently breaks nearest-heading
    # tracking for every figure/table in every document.
    assert _LABEL_MAP["section_header"] == ElementType.HEADING


def _fake_item(label: str, text: str | None = None, prov: list | None = None):
    obj = types.SimpleNamespace(label=label, prov=prov or [])
    if text is not None:
        obj.text = text
    return obj


def test_heading_tracking_and_figure_text_guard(monkeypatch):
    """A real Docling section header must update nearest_heading, and a
    PictureItem's `.text` attribute — even when populated with the item's raw
    repr (a real Docling behaviour observed in production) — must never leak
    into the extracted element's text; only a later VLM caption may."""
    heading_item = _fake_item("section_header", text="3.1 Pricing Grid")
    picture_item = _fake_item(
        "picture",
        text="self_ref='#/pictures/2' label=<DocItemLabel.PICTURE> ...garbage...",
    )

    fake_doc = types.SimpleNamespace(
        iterate_items=lambda: iter([(heading_item, 0), (picture_item, 0)]),
        export_to_markdown=lambda: "# doc",
        num_pages=lambda: 1,  # real DoclingDocument.num_pages is a method, not a property
    )
    fake_result = types.SimpleNamespace(document=fake_doc)
    fake_converter = types.SimpleNamespace(convert=lambda path: fake_result)

    extractor = DoclingExtractor(enrichment=None)
    monkeypatch.setattr(extractor, "_get_converter", lambda do_ocr: fake_converter)

    result = extractor._sync_extract(Path("fake.docx"))

    heading_el, picture_el = result.elements
    assert heading_el.element_type == ElementType.HEADING
    assert picture_el.element_type == ElementType.FIGURE
    assert picture_el.text == ""  # garbage .text must not leak into the chunk
    assert picture_el.metadata["nearest_heading"] == "3.1 Pricing Grid"
