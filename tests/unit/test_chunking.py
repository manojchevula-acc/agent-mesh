"""Unit tests for the chunking module."""

from gernas_rag.chunking.factory import get_chunker
from gernas_rag.chunking.hierarchical import HierarchicalChunker
from gernas_rag.config.chunking import ChunkingConfig, ChunkingStrategy


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
