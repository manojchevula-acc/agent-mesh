"""Unit tests for the chunking module."""

from gernas_rag.chunking.factory import get_chunker
from gernas_rag.chunking.hierarchical import HierarchicalChunker, _is_fragmented_table
from gernas_rag.config.chunking import ChunkingConfig, ChunkingStrategy
from gernas_rag.extraction.base import ExtractionResult


def _base_metadata() -> dict:
    return {
        "document_name": "FAB_Credit_Pricing_Policy_v2.4",
        "document_type": "pricing_policy",
        "product_applicability": ["corporate_loan"],
        "effective_date": "2024-06-01",
    }


def test_hierarchical_chunker_produces_parent_and_child(sample_extraction):
    config = ChunkingConfig(enable_parent_chunks=True)
    chunker = HierarchicalChunker(config)
    chunks = chunker.chunk(sample_extraction, _base_metadata())

    assert chunks, "expected at least one chunk"
    parents = [c for c in chunks if c.is_parent]
    children = [c for c in chunks if not c.is_parent]
    assert parents, "expected parent chunks"
    assert children, "expected child chunks"
    # Children reference a parent.
    assert all(c.metadata.parent_chunk_id for c in children)


def test_chunk_ids_are_deterministic(sample_extraction):
    config = ChunkingConfig()
    a = HierarchicalChunker(config).chunk(sample_extraction, _base_metadata())
    b = HierarchicalChunker(config).chunk(sample_extraction, _base_metadata())
    assert [c.id for c in a] == [c.id for c in b]


def test_clause_reference_extraction(sample_extraction):
    config = ChunkingConfig()
    chunks = HierarchicalChunker(config).chunk(sample_extraction, _base_metadata())
    refs = {c.metadata.clause_reference for c in chunks if not c.is_parent}
    assert any(r and r[0].isdigit() for r in refs)


def test_fixed_size_chunker_has_no_parents(sample_extraction):
    config = ChunkingConfig(strategy=ChunkingStrategy.FIXED_SIZE, enable_parent_chunks=False)
    chunker = get_chunker(config)
    chunks = chunker.chunk(sample_extraction, _base_metadata())
    assert chunks
    assert all(not c.is_parent for c in chunks)


def test_factory_returns_hierarchical_by_default():
    chunker = get_chunker(ChunkingConfig())
    assert isinstance(chunker, HierarchicalChunker)


def test_child_chunk_uses_its_own_heading_not_the_parents():
    """A child cut from deep inside a parent block must not inherit an earlier,
    unrelated section's heading/clause just because the parent's first heading was
    that earlier section.

    Regression for a production bug: a real chunk whose text opened "## 5.1
    Required Documentation for Credit Approval" was stored with
    section_heading="3.2 Pricing Floors..." and clause_reference="3.2" — the
    *parent* block's opening section — because the child heading/clause were
    always taken from the parent, never re-derived from the child's own text.
    Citations pointed at the wrong clause for every such chunk.
    """
    filler = " ".join(
        f"This is filler sentence number {i} padding out section 3.2 pricing "
        "floor content for the corporate term loan product."
        for i in range(28)
    )
    markdown = (
        "## 3.2 Pricing Floors - Reference to Credit Pricing Policy\n\n"
        f"{filler}\n\n"
        "## 5. Documentation and Credit Approval\n\n"
        "## 5.1 Required Documentation for Credit Approval\n\n"
        "- Completed Credit Application Form (CAF) - FAB-CAF-CORP-2024.\n"
        "- Audited financial statements - minimum 3 years.\n"
    )
    extraction = ExtractionResult(
        elements=[], raw_markdown=markdown, page_count=1, file_path="x.pdf"
    )
    chunks = HierarchicalChunker(ChunkingConfig()).chunk(extraction, _base_metadata())
    children = [c for c in chunks if not c.is_parent]

    doc_child = next(c for c in children if "Documentation and Credit Approval" in c.text)
    assert doc_child.metadata.section_heading.startswith("5.")
    assert doc_child.metadata.clause_reference == "5.1"
    # And the earlier section's own chunks must still correctly report themselves.
    floor_child = next(c for c in children if c.text.startswith("This is filler"))
    assert floor_child.metadata.clause_reference == "3.2"


def test_long_table_split_across_chunks_keeps_header_in_every_piece():
    # A high-confidence table stays as plain markdown and goes through the same
    # RecursiveCharacterTextSplitter as prose; long enough here to force a
    # mid-table split (eval stage2a's TABLE_FRAGMENTED regression).
    rows = "\n".join(
        f"| Row {i} | Value {i} | Some longer descriptive text for row {i} to pad length |"
        for i in range(80)
    )
    markdown = f"# Section 6\n\n| Column A | Column B | Column C |\n|---|---|---|\n{rows}\n"
    extraction = ExtractionResult(
        elements=[], raw_markdown=markdown, page_count=1, file_path="x.pdf"
    )
    chunks = HierarchicalChunker(ChunkingConfig()).chunk(extraction, _base_metadata())
    children = [c for c in chunks if not c.is_parent]

    assert len(children) > 1, "test setup should force a split across multiple chunks"
    assert not any(_is_fragmented_table(c.text) for c in children)
