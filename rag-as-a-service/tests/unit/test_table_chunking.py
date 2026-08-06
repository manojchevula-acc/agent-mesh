"""D8 regression suite — tables must be atomic and never lose their header.

The defect this guards against: RecursiveCharacterTextSplitter has '\\n' in its
separator list, and markdown table rows are newline-separated, so before D8 a
long table was split mid-table leaving rows with no header — uninterpretable, and
an invitation to a confidently wrong answer.
"""

import pytest

from gernas_rag.chunking.base import BaseChunker
from gernas_rag.chunking.fixed_size import FixedSizeChunker
from gernas_rag.chunking.hierarchical import HierarchicalChunker
from gernas_rag.config.chunking import ChunkingConfig
from gernas_rag.extraction.base import ExtractionResult

_BASE_META = {
    "document_name": "doc",
    "document_type": "pricing_policy",
    "product_applicability": [],
    "effective_date": "",
}


def _extraction(markdown: str) -> ExtractionResult:
    return ExtractionResult(
        elements=[], raw_markdown=markdown, page_count=1, file_path="doc.pdf"
    )


def _chunk(markdown: str, **config_overrides):
    config = ChunkingConfig(**config_overrides)
    return HierarchicalChunker(config).chunk(_extraction(markdown), _BASE_META)


def _tables(chunks):
    return [c for c in chunks if c.metadata.content_type == "table"]


def _prose(chunks):
    return [c for c in chunks if c.metadata.content_type == "text"]


# ── Atomicity ────────────────────────────────────────────────────────────
def test_table_yields_exactly_one_chunk(sample_table_markdown):
    tables = _tables(_chunk(sample_table_markdown))
    assert len(tables) == 1
    text = tables[0].text
    # Every row survives in the same chunk.
    for rating in ("AAA", "AA", "A", "BBB", "BB", "B"):
        assert f"| {rating}" in text


def test_table_keeps_its_delimiter_row(sample_table_markdown):
    assert "| ------" in _tables(_chunk(sample_table_markdown))[0].text


def test_table_does_not_leak_into_prose_chunks(sample_table_markdown):
    for chunk in _prose(_chunk(sample_table_markdown)):
        assert "|" not in chunk.text


def test_placeholders_never_reach_the_index(sample_table_markdown):
    for chunk in _chunk(sample_table_markdown):
        assert "[[TABLE" not in chunk.text


def test_caption_is_captured_as_prefix(sample_table_markdown):
    assert "Minimum pricing floors by rating" in _tables(_chunk(sample_table_markdown))[0].text


def test_caption_never_picks_up_a_row_of_the_previous_table():
    """Docling splits a page-spanning table into two adjacent pipe-blocks; the
    caption resolver must not treat the first block's last ROW as the second
    block's title. Observed on FAB_Credit_Concentration_Limits_Policy_v1_8.pdf.
    """
    markdown = (
        "Figure 2.1 - Exposure by counterparty\n\n"
        "| Class | Max |\n| ----- | --- |\n| GRE   | 5bn |\n| Bank  | 2bn |\n"
        "\n"
        "| Class | Max |\n| ----- | --- |\n| FI    | 1bn |\n| Corp  | 3bn |\n"
    )
    tables = _tables(_chunk(markdown))
    assert len(tables) == 2
    for chunk in tables:
        caption_line = chunk.text.split("\n", 1)[0]
        assert "|" not in caption_line, f"row leaked into caption: {caption_line!r}"


# ── Row splitting with header repetition ─────────────────────────────────
def test_oversized_table_splits_with_repeated_header():
    header = "| Rating | Tenor | AED bps |\n| ------ | ----- | ------- |\n"
    rows = "".join(f"| R{i:03d}   | 3-5y  | {200 + i}     |\n" for i in range(200))
    markdown = f"Table 9: Long schedule\n\n{header}{rows}\nTrailing prose paragraph.\n"

    tables = _tables(_chunk(markdown, max_chunk_size=100))
    assert len(tables) > 1, "expected the long table to be row-split"

    for chunk in tables:
        body = chunk.text.split("\n\n", 1)[1]
        assert body.startswith("| Rating | Tenor | AED bps |"), "header not repeated"
        assert "| ------" in body, "delimiter not repeated"
        assert chunk.metadata.table_part is not None

    parts = [c.metadata.table_part for c in tables]
    assert parts == [f"{i + 1}/{len(tables)}" for i in range(len(tables))]


def test_split_preserves_every_row():
    header = "| K | V |\n| - | - |\n"
    rows = "".join(f"| k{i} | v{i} |\n" for i in range(80))
    tables = _tables(_chunk(f"Table 1: rows\n\n{header}{rows}\nEnd.\n", max_chunk_size=60))
    seen = {
        line for c in tables for line in c.text.split("\n") if line.startswith("| k")
    }
    assert len(seen) == 80


def test_single_row_table_is_not_split():
    markdown = "Intro.\n\n| A | B |\n| - | - |\n| 1 | 2 |\n\nOutro.\n"
    tables = _tables(_chunk(markdown))
    assert len(tables) == 1
    assert tables[0].metadata.table_part is None


# ── False positives ──────────────────────────────────────────────────────
def test_prose_with_pipes_is_not_treated_as_a_table():
    """The delimiter row is required, which prose essentially never contains."""
    markdown = (
        "# Policy\n\nThe grammar is a | b | c and the operator | is used widely "
        "throughout this clause. Pipes | appear | often in this paragraph but "
        "there is no delimiter row anywhere in the document at all.\n"
    )
    assert _tables(_chunk(markdown)) == []


def test_table_needs_a_body_row():
    markdown = "Intro.\n\n| A | B |\n| - | - |\n\nOutro text follows here.\n"
    assert _tables(_chunk(markdown)) == []


# ── Feature flag ─────────────────────────────────────────────────────────
def test_protect_tables_false_restores_previous_behaviour(sample_table_markdown):
    off = _chunk(sample_table_markdown, protect_tables=False)
    assert _tables(off) == []
    # The table text is back inside ordinary prose chunks, as it was pre-D8.
    assert any("| BBB" in c.text for c in off)


# ── Metadata ─────────────────────────────────────────────────────────────
def test_table_metadata_is_populated(sample_table_markdown):
    table = _tables(_chunk(sample_table_markdown))[0]
    assert table.metadata.content_type == "table"
    assert table.metadata.table_rows == 6
    assert table.metadata.parent_chunk_id is not None


def test_row_count_excludes_header_and_delimiter():
    assert BaseChunker._count_table_rows("| A |\n| - |\n| 1 |\n| 2 |") == 2


# ── Fallback chunker ─────────────────────────────────────────────────────
def test_fixed_size_chunker_also_protects_tables(sample_table_markdown):
    chunks = FixedSizeChunker(ChunkingConfig()).chunk(
        _extraction(sample_table_markdown), _BASE_META
    )
    tables = _tables(chunks)
    assert len(tables) == 1
    assert "| BBB" in tables[0].text
    for chunk in _prose(chunks):
        assert "|" not in chunk.text


# ── Helper unit tests ────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "markdown,expected",
    [
        ("| A | B |\n| - | - |\n| 1 | 2 |\n", 1),
        ("no table here at all\n", 0),
        ("| A |\n| - |\n| 1 |\n\ntext\n\n| C |\n| - |\n| 2 |\n", 2),
    ],
)
def test_mask_tables_counts(markdown, expected):
    _, blocks = BaseChunker._mask_tables(markdown)
    assert len(blocks) == expected


def test_strip_placeholders_collapses_blank_lines():
    assert BaseChunker._strip_placeholders("a\n\n[[TABLE_0]]\n\nb") == "a\n\nb"
