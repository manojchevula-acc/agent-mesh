"""Unit tests for the extraction module (factory + interfaces)."""

from pathlib import Path

from gernas_rag.config.chunking import ChunkingConfig, ExtractionStrategy
from gernas_rag.extraction.base import ElementType, ExtractedElement, ExtractionResult
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
