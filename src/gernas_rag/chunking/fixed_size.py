"""Fallback chunker — naive fixed-token chunks."""

from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from ..config.chunking import ChunkingConfig
from ..extraction.base import ExtractionResult
from ..models.chunk import Chunk, ChunkMetadata
from ..utils.hashing import make_chunk_id
from ..utils.logging import get_logger
from .base import BaseChunker

logger = get_logger(__name__)

_CHARS_PER_TOKEN = 4


class FixedSizeChunker(BaseChunker):
    """Splits text into fixed-size overlapping chunks with no parent hierarchy."""

    def __init__(self, config: ChunkingConfig) -> None:
        self._config = config
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=config.chunk_size * _CHARS_PER_TOKEN,
            chunk_overlap=config.chunk_overlap * _CHARS_PER_TOKEN,
            length_function=len,
        )

    def chunk(self, extraction: ExtractionResult, base_metadata: dict[str, Any]) -> list[Chunk]:
        text = extraction.raw_markdown
        doc_name = base_metadata["document_name"]
        chunks: list[Chunk] = []
        for i, piece in enumerate(self._splitter.split_text(text)):
            if len(piece.split()) < self._config.min_chunk_size // _CHARS_PER_TOKEN:
                continue
            heading = self._extract_heading(piece)
            clause_ref = self._extract_clause_ref(piece, heading)
            meta = ChunkMetadata(
                **{
                    **base_metadata,
                    "clause_reference": clause_ref or str(i),
                    "section_heading": heading,
                }
            )
            chunks.append(Chunk(id=make_chunk_id(doc_name, str(i)), text=piece, metadata=meta))
        logger.info("Chunking complete", total_chunks=len(chunks), document=doc_name)
        return chunks

