"""Primary chunker — hierarchical (parent/child) splitting, table-aware."""

from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from ..config.chunking import ChunkingConfig
from ..extraction.base import ExtractionResult
from ..models.asset import ContentType
from ..models.chunk import Chunk, ChunkMetadata
from ..utils.hashing import make_chunk_id
from ..utils.logging import get_logger
from .base import BaseChunker, TableBlock

logger = get_logger(__name__)

# Approximate characters-per-token for converting token budgets to char budgets.
_CHARS_PER_TOKEN = 4


class HierarchicalChunker(BaseChunker):
    """Splits document text at semantic boundaries — heading hierarchy, numbered
    clauses, paragraph breaks. Never splits mid-sentence. Produces parent + child
    chunk pairs for multi-vector retrieval.

    Tables are ATOMIC (D8): markdown tables are lifted out before splitting and
    re-attached as their own chunks, so the character splitter can never break a
    table between rows and strand rows without their header.
    """

    def __init__(self, config: ChunkingConfig) -> None:
        self._config = config
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=config.chunk_size * _CHARS_PER_TOKEN,
            chunk_overlap=config.chunk_overlap * _CHARS_PER_TOKEN,
            separators=config.separators,
            is_separator_regex=True,
            length_function=len,
            keep_separator=True,
        )
        self._parent_splitter = RecursiveCharacterTextSplitter(
            chunk_size=config.parent_chunk_size * _CHARS_PER_TOKEN,
            chunk_overlap=0,
            separators=[r"\n#{1,2} ", r"\n\n"],
            is_separator_regex=True,
            length_function=len,
        )

    def chunk(self, extraction: ExtractionResult, base_metadata: dict[str, Any]) -> list[Chunk]:
        """Takes an ExtractionResult, returns a list of Chunk objects.

        Each chunk has a deterministic ID and full metadata. If parent chunking is
        enabled, parent chunks (``is_parent=True``) are also returned. Tables are
        emitted as separate atomic chunks with ``content_type='table'``.
        """
        raw_text = extraction.raw_markdown
        doc_name = base_metadata["document_name"]

        # Step 1: lift tables out BEFORE any splitting.
        if self._config.protect_tables:
            text, tables = self._mask_tables(
                raw_text, self._config.table_caption_window_chars
            )
        else:
            text, tables = raw_text, []

        # Step 2: chunk the prose exactly as before.
        chunks, table_owner = (
            self._chunk_with_parents(text, base_metadata, doc_name)
            if self._config.enable_parent_chunks
            else self._chunk_flat(text, base_metadata, doc_name)
        )

        # Step 3: re-attach tables as their own atomic chunks.
        for block in tables:
            chunks.extend(
                self._table_chunks(block, table_owner.get(block.key), base_metadata, doc_name)
            )

        logger.info(
            "Chunking complete",
            total_chunks=len(chunks),
            tables=len(tables),
            document=doc_name,
        )
        return chunks

    # ── Prose chunking ────────────────────────────────────────────────
    def _chunk_with_parents(
        self, text: str, base_metadata: dict[str, Any], doc_name: str
    ) -> tuple[list[Chunk], dict[str, Chunk]]:
        """Parent/child splitting. Also records which parent owns each table."""
        chunks: list[Chunk] = []
        table_owner: dict[str, Chunk] = {}

        for pi, parent_raw in enumerate(self._parent_splitter.split_text(text)):
            parent_text = self._strip_placeholders(parent_raw)
            parent_heading = self._extract_heading(parent_raw)
            parent_clause = self._extract_clause_ref(parent_raw, parent_heading)
            parent_id = make_chunk_id(doc_name, f"parent_{pi}")
            parent_meta = ChunkMetadata(
                **{
                    **base_metadata,
                    "clause_reference": parent_clause or f"section_{pi}",
                    "section_heading": parent_heading,
                }
            )
            parent = Chunk(
                id=parent_id, text=parent_text, metadata=parent_meta, is_parent=True
            )
            # Tables inside this parent inherit its heading / clause context.
            for key in self._placeholders_in(parent_raw):
                table_owner[key] = parent

            # A parent that held nothing but a table has no prose worth indexing.
            if parent_text:
                chunks.append(parent)

            for ci, child_raw in enumerate(self._splitter.split_text(parent_raw)):
                child_text = self._strip_placeholders(child_raw)
                if not child_text:
                    continue  # Placeholder-only fragment
                if len(child_text.split()) < self._config.min_chunk_size // 4:
                    continue  # Skip too-small chunks
                clause_ref = self._extract_clause_ref(child_text, parent_heading)
                child_id = make_chunk_id(doc_name, f"p{pi}_c{ci}")
                child_meta = ChunkMetadata(
                    **{
                        **base_metadata,
                        "clause_reference": clause_ref or parent_clause or f"p{pi}_c{ci}",
                        "section_heading": parent_heading,
                        "parent_chunk_id": parent_id,
                    }
                )
                chunks.append(Chunk(id=child_id, text=child_text, metadata=child_meta))

        return chunks, table_owner

    def _chunk_flat(
        self, text: str, base_metadata: dict[str, Any], doc_name: str
    ) -> tuple[list[Chunk], dict[str, Chunk]]:
        chunks: list[Chunk] = []
        table_owner: dict[str, Chunk] = {}

        for i, piece_raw in enumerate(self._splitter.split_text(text)):
            piece = self._strip_placeholders(piece_raw)
            heading = self._extract_heading(piece_raw)
            clause_ref = self._extract_clause_ref(piece_raw, heading)
            chunk_id = make_chunk_id(doc_name, str(i))
            meta = ChunkMetadata(
                **{
                    **base_metadata,
                    "clause_reference": clause_ref or str(i),
                    "section_heading": heading,
                }
            )
            chunk = Chunk(id=chunk_id, text=piece, metadata=meta)
            for key in self._placeholders_in(piece_raw):
                table_owner[key] = chunk
            if piece:
                chunks.append(chunk)

        return chunks, table_owner

    # ── Table chunking (D8) ───────────────────────────────────────────
    def _table_chunks(
        self,
        block: TableBlock,
        owner: Chunk | None,
        base_metadata: dict[str, Any],
        doc_name: str,
    ) -> list[Chunk]:
        """One chunk per table, or several row-groups with repeated headers."""
        budget = self._config.max_chunk_size * _CHARS_PER_TOKEN
        parts = self._split_table_by_rows(block.markdown, budget)
        heading = owner.metadata.section_heading if owner else ""
        clause = owner.metadata.clause_reference if owner else block.key.lower()
        caption = block.caption or heading

        out: list[Chunk] = []
        for i, part in enumerate(parts):
            # The prefix restores context the grid itself does not carry: a bare
            # table of numbers has almost no semantic signal for dense retrieval.
            body = f"[Table] {caption}\n\n{part}" if caption else f"[Table]\n\n{part}"
            out.append(
                Chunk(
                    id=make_chunk_id(doc_name, f"{block.key.lower()}_p{i}"),
                    text=body,
                    metadata=ChunkMetadata(
                        **{
                            **base_metadata,
                            "clause_reference": clause,
                            "section_heading": heading,
                            "parent_chunk_id": owner.id if owner else None,
                            "content_type": ContentType.TABLE.value,
                            "table_rows": self._count_table_rows(part),
                            "table_part": f"{i + 1}/{len(parts)}" if len(parts) > 1 else None,
                        }
                    ),
                )
            )
        return out
