"""Fallback chunker — naive fixed-token chunks, table-aware."""

from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from ..config.chunking import ChunkingConfig
from ..extraction.base import ExtractionResult
from ..models.asset import ContentType
from ..models.chunk import Chunk, ChunkMetadata
from ..utils.hashing import make_chunk_id
from ..utils.logging import get_logger
from .base import BaseChunker

logger = get_logger(__name__)

_CHARS_PER_TOKEN = 4


class FixedSizeChunker(BaseChunker):
    """Splits text into fixed-size overlapping chunks with no parent hierarchy.

    Shares the table-protection helpers on :class:`BaseChunker`, so tables stay
    atomic here too (D8).
    """

    def __init__(self, config: ChunkingConfig) -> None:
        self._config = config
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=config.chunk_size * _CHARS_PER_TOKEN,
            chunk_overlap=config.chunk_overlap * _CHARS_PER_TOKEN,
            length_function=len,
        )

    def chunk(self, extraction: ExtractionResult, base_metadata: dict[str, Any]) -> list[Chunk]:
        raw_text = extraction.raw_markdown
        doc_name = base_metadata["document_name"]

        if self._config.protect_tables:
            text, tables = self._mask_tables(
                raw_text, self._config.table_caption_window_chars
            )
        else:
            text, tables = raw_text, []

        chunks: list[Chunk] = []
        owner_heading: dict[str, str] = {}

        for i, piece_raw in enumerate(self._splitter.split_text(text)):
            heading = self._extract_heading(piece_raw)
            for key in self._placeholders_in(piece_raw):
                owner_heading[key] = heading
            piece = self._strip_placeholders(piece_raw)
            if not piece:
                continue
            if len(piece.split()) < self._config.min_chunk_size // _CHARS_PER_TOKEN:
                continue
            clause_ref = self._extract_clause_ref(piece, heading)
            meta = ChunkMetadata(
                **{
                    **base_metadata,
                    "clause_reference": clause_ref or str(i),
                    "section_heading": heading,
                }
            )
            chunks.append(Chunk(id=make_chunk_id(doc_name, str(i)), text=piece, metadata=meta))

        budget = self._config.max_chunk_size * _CHARS_PER_TOKEN
        for block in tables:
            heading = owner_heading.get(block.key, "")
            caption = block.caption or heading
            parts = self._split_table_by_rows(block.markdown, budget)
            for pi, part in enumerate(parts):
                body = f"[Table] {caption}\n\n{part}" if caption else f"[Table]\n\n{part}"
                chunks.append(
                    Chunk(
                        id=make_chunk_id(doc_name, f"{block.key.lower()}_p{pi}"),
                        text=body,
                        metadata=ChunkMetadata(
                            **{
                                **base_metadata,
                                "clause_reference": block.key.lower(),
                                "section_heading": heading,
                                "content_type": ContentType.TABLE.value,
                                "table_rows": self._count_table_rows(part),
                                "table_part": (
                                    f"{pi + 1}/{len(parts)}" if len(parts) > 1 else None
                                ),
                            }
                        ),
                    )
                )

        logger.info(
            "Chunking complete", total_chunks=len(chunks), tables=len(tables), document=doc_name
        )
        return chunks
