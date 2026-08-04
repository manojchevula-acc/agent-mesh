"""Unit tests for atomic media-chunk construction (image-as-text)."""

from gernas_rag.chunking.hierarchical import HierarchicalChunker
from gernas_rag.config.chunking import ChunkingConfig
from gernas_rag.extraction.base import ElementType, ExtractedElement, ExtractionResult
from gernas_rag.models.chunk import Modality


def _base_meta():
    return {
        "document_name": "FAB Pricing Policy v2.4",
        "document_type": "pricing_policy",
        "product_applicability": [],
        "effective_date": "",
    }


def _extraction(elements, markdown="# Doc\n\nSome text.\n"):
    return ExtractionResult(
        elements=elements, raw_markdown=markdown, page_count=1, file_path="x.pdf"
    )


def test_enriched_figure_becomes_one_atomic_chunk():
    chunker = HierarchicalChunker(ChunkingConfig())
    long_caption = " ".join(f"row{i}: value {i}" for i in range(400))  # well over the 1600-char window
    fig = ExtractedElement(
        element_type=ElementType.FIGURE,
        text=long_caption,
        page_number=3,
        bbox=(10.0, 20.0, 300.0, 400.0),
        metadata={
            "nearest_heading": "3.1 Pricing Grid",
            "artifact_ref": "sha256:deadbeef.png",
            "enrichment_model": "claude-haiku-4-5-20251001",
        },
    )
    chunks = chunker.chunk(_extraction([fig]), _base_meta())

    media = [c for c in chunks if c.metadata.modality == Modality.FIGURE]
    assert len(media) == 1  # exactly one chunk, never fragmented
    mc = media[0]
    assert mc.text == long_caption  # full caption preserved, not truncated/split
    assert mc.metadata.artifact_ref == "sha256:deadbeef.png"
    assert mc.metadata.enrichment_model == "claude-haiku-4-5-20251001"
    assert mc.metadata.source_page == 3
    assert mc.metadata.section_heading == "3.1 Pricing Grid"
    assert mc.metadata.bbox == (10.0, 20.0, 300.0, 400.0)  # carried through for citation/re-cropping


def test_media_chunk_without_bbox_defaults_to_none():
    chunker = HierarchicalChunker(ChunkingConfig())
    fig = ExtractedElement(
        element_type=ElementType.FIGURE,
        text="A chart",
        metadata={"artifact_ref": "sha256:abc.png"},
    )
    chunks = chunker.chunk(_extraction([fig]), _base_meta())
    media = [c for c in chunks if c.metadata.modality == Modality.FIGURE][0]
    assert media.metadata.bbox is None


def test_media_chunk_id_is_deterministic():
    chunker = HierarchicalChunker(ChunkingConfig())
    fig = ExtractedElement(
        element_type=ElementType.FIGURE,
        text="A chart",
        metadata={"artifact_ref": "sha256:abc.png"},
    )
    a = chunker.chunk(_extraction([fig]), _base_meta())
    b = chunker.chunk(_extraction([fig]), _base_meta())
    a_media = [c for c in a if c.metadata.modality == Modality.FIGURE][0]
    b_media = [c for c in b if c.metadata.modality == Modality.FIGURE][0]
    assert a_media.id == b_media.id  # idempotent re-ingestion


def test_unenriched_element_produces_no_media_chunk():
    chunker = HierarchicalChunker(ChunkingConfig())
    # A figure without an artifact_ref (enrichment skipped/failed) is not indexed.
    fig = ExtractedElement(
        element_type=ElementType.FIGURE, text="", metadata={"nearest_heading": "X"}
    )
    chunks = chunker.chunk(_extraction([fig]), _base_meta())
    assert all(c.metadata.modality == Modality.TEXT for c in chunks)


def test_text_chunks_still_produced():
    chunker = HierarchicalChunker(ChunkingConfig())
    md = "# Section 1\n\n" + ("This is policy text. " * 60)
    chunks = chunker.chunk(_extraction([], markdown=md), _base_meta())
    assert any(c.metadata.modality == Modality.TEXT for c in chunks)


def test_media_chunk_links_to_parent_with_matching_heading():
    # eval stage2a's MEDIA_HAS_NO_PARENT: a figure's nearest_heading should join
    # to the parent chunk that starts on that same heading, so retrieval's parent
    # expansion returns real surrounding prose for a figure, not nothing.
    chunker = HierarchicalChunker(ChunkingConfig(enable_parent_chunks=True))
    md = "# 3.1 Pricing Grid\n\n" + ("Some section prose. " * 40)
    fig = ExtractedElement(
        element_type=ElementType.FIGURE,
        text="A chart",
        page_number=3,
        metadata={"nearest_heading": "3.1 Pricing Grid", "artifact_ref": "sha256:deadbeef.png"},
    )
    chunks = chunker.chunk(_extraction([fig], markdown=md), _base_meta())

    parent = next(c for c in chunks if c.is_parent)
    media = next(c for c in chunks if c.metadata.modality == Modality.FIGURE)
    assert media.metadata.parent_chunk_id == parent.id


def test_media_chunk_with_no_matching_heading_has_no_parent():
    chunker = HierarchicalChunker(ChunkingConfig(enable_parent_chunks=True))
    md = "# 3.1 Pricing Grid\n\n" + ("Some section prose. " * 40)
    fig = ExtractedElement(
        element_type=ElementType.FIGURE,
        text="A chart",
        page_number=9,
        metadata={"nearest_heading": "9.9 Nonexistent Section", "artifact_ref": "sha256:cafe.png"},
    )
    chunks = chunker.chunk(_extraction([fig], markdown=md), _base_meta())
    media = next(c for c in chunks if c.metadata.modality == Modality.FIGURE)
    assert media.metadata.parent_chunk_id is None
