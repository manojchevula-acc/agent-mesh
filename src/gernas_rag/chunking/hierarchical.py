"""Primary chunker — hierarchical (parent/child) splitting."""

from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from ..config.chunking import ChunkingConfig
from ..extraction.base import ExtractionResult
from ..models.chunk import Chunk, ChunkMetadata
from ..utils.hashing import make_chunk_id
from ..utils.logging import get_logger
from .base import BaseChunker

logger = get_logger(__name__)

# Approximate characters-per-token for converting token budgets to char budgets.
_CHARS_PER_TOKEN = 4


class HierarchicalChunker(BaseChunker):
    """Splits document text at semantic boundaries — heading hierarchy, numbered
    clauses, paragraph breaks. Never splits mid-sentence. Produces parent + child
    chunk pairs for multi-vector retrieval.
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
        enabled, parent chunks (``is_parent=True``) are also returned.
        """
        text = extraction.raw_markdown
        doc_name = base_metadata["document_name"]
        chunks: list[Chunk] = []

        if self._config.enable_parent_chunks:
            parent_texts = self._parent_splitter.split_text(text)
            for pi, parent_text in enumerate(parent_texts):
                parent_heading = self._extract_heading(parent_text)
                parent_clause = self._extract_clause_ref(parent_text, parent_heading)
                parent_id = make_chunk_id(doc_name, f"parent_{pi}")
                parent_meta = ChunkMetadata(
                    **{
                        **base_metadata,
                        "clause_reference": parent_clause or f"section_{pi}",
                        "section_heading": parent_heading,
                    }
                )
                chunks.append(
                    Chunk(id=parent_id, text=parent_text, metadata=parent_meta, is_parent=True)
                )

                for ci, child_text in enumerate(self._splitter.split_text(parent_text)):
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
        else:
            for i, child_text in enumerate(self._splitter.split_text(text)):
                heading = self._extract_heading(child_text)
                clause_ref = self._extract_clause_ref(child_text, heading)
                chunk_id = make_chunk_id(doc_name, str(i))
                meta = ChunkMetadata(
                    **{
                        **base_metadata,
                        "clause_reference": clause_ref or str(i),
                        "section_heading": heading,
                    }
                )
                chunks.append(Chunk(id=chunk_id, text=child_text, metadata=meta))

        logger.info("Chunking complete", total_chunks=len(chunks), document=doc_name)
        return chunks

