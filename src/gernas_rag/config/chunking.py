"""Chunking and extraction configuration."""

from enum import Enum

from pydantic import BaseModel, Field


class ChunkingStrategy(str, Enum):
    HIERARCHICAL = "hierarchical"  # Primary — respects document structure
    FIXED_SIZE = "fixed_size"  # Fallback — naive fixed-token chunks


class ExtractionStrategy(str, Enum):
    DOCLING = "docling"  # Primary — structure-preserving
    UNSTRUCTURED = "unstructured"  # Fallback — scanned PDFs / mixed formats
    PYMUPDF = "pymupdf"  # Utility — fast raw text extraction
    AUTO = "auto"  # Auto-select based on file type


class ChunkingConfig(BaseModel):
    strategy: ChunkingStrategy = ChunkingStrategy.HIERARCHICAL
    extraction_strategy: ExtractionStrategy = ExtractionStrategy.AUTO

    # ── Chunk sizes ───────────────────────────────────────────────────
    chunk_size: int = 400  # Target chunk size in tokens
    chunk_overlap: int = 64  # Overlap between adjacent chunks
    min_chunk_size: int = 80  # Merge chunks smaller than this
    max_chunk_size: int = 600  # Hard ceiling — split if exceeded

    # ── Parent-child chunking ──────────────────────────────────────────
    enable_parent_chunks: bool = True
    parent_chunk_size: int = 1500  # Full section size

    # ── Boundary detection ────────────────────────────────────────────
    separators: list[str] = Field(
        default_factory=lambda: [
            r"\n#{1,3} ",  # Markdown headings
            r"\n\d+\.\d+\.\d+\s",  # Numbered sub-clauses: 4.2.1
            r"\n\d+\.\d+\s",  # Sub-clauses: 4.2
            r"\nArticle \d+",  # CBUAE article markers
            r"\nSection \d+",  # Section markers
            r"\n\n",  # Paragraph break
            r"\n",  # Line break
            r" ",  # Word break (last resort)
        ]
    )

    # ── Unstructured.io specific ──────────────────────────────────────
    unstructured_strategy: str = "hi_res"  # 'fast' | 'hi_res' | 'ocr_only'
    infer_table_structure: bool = True
