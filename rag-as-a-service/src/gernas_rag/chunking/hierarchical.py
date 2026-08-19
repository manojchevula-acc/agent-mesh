"""Primary chunker — hierarchical (parent/child) splitting."""

import re
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from ..config.chunking import ChunkingConfig
from ..extraction.base import ExtractedElement, ExtractionResult
from ..models.chunk import Chunk, ChunkMetadata, Modality
from ..utils.hashing import make_chunk_id
from ..utils.logging import get_logger
from .base import BaseChunker

logger = get_logger(__name__)

# Approximate characters-per-token for converting token budgets to char budgets.
_CHARS_PER_TOKEN = 4

# A markdown table header: a row immediately followed by its delimiter row
# (e.g. "| A | B |\n|---|---|"). RecursiveCharacterTextSplitter has no notion of
# table structure, so a table longer than chunk_size gets cut between rows —
# every row after the cut loses its header and becomes uninterpretable on its
# own at retrieval time (eval/stage2_enrichment's TABLE_FRAGMENTED finding).
_TABLE_ROW = re.compile(r"^[ \t]*\|.*\|[ \t]*$", re.MULTILINE)
_TABLE_SEPARATOR = re.compile(r"^[ \t]*\|[\s:|-]+\|[ \t]*$", re.MULTILINE)
_TABLE_HEADER_BLOCK = re.compile(
    r"^[ \t]*\|.*\|[ \t]*\r?\n^[ \t]*\|[\s:|-]+\|[ \t]*$", re.MULTILINE
)


def _is_fragmented_table(text: str) -> bool:
    """True when *text* holds table rows but not the header that defines them."""
    if len(_TABLE_ROW.findall(text)) < 2:
        return False
    return not _TABLE_SEPARATOR.search(text)


def _repair_fragmented_table(child_text: str, container_text: str) -> str:
    """Re-attach a table's header to a chunk that only got the tail rows.

    Finds the nearest table header block in *container_text* that precedes
    *child_text*'s position and prepends it, so the chunk is self-contained
    even when split away from its header. Left unchanged if the header can't be
    located (e.g. the splitter's overlap altered the substring) rather than
    guessing at the wrong table's header.
    """
    if not _is_fragmented_table(child_text):
        return child_text
    idx = container_text.find(child_text)
    if idx == -1:
        return child_text
    preceding_headers = [
        m.group(0) for m in _TABLE_HEADER_BLOCK.finditer(container_text) if m.end() <= idx
    ]
    if not preceding_headers:
        return child_text
    return f"{preceding_headers[-1]}\n{child_text}"


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
        # Heading -> parent chunk id, used to link a media element to the parent
        # section it visually belongs to (see _build_media_chunks).
        parent_id_by_heading: dict[str, str] = {}

        if self._config.enable_parent_chunks:
            parent_texts = self._parent_splitter.split_text(text)
            for pi, parent_text in enumerate(parent_texts):
                parent_heading = self._extract_heading(parent_text)
                parent_clause = self._extract_clause_ref(parent_text, parent_heading)
                parent_id = make_chunk_id(doc_name, f"parent_{pi}")
                if parent_heading:
                    parent_id_by_heading.setdefault(parent_heading.strip().lower(), parent_id)
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
                    child_text = _repair_fragmented_table(child_text, parent_text)
                    # A parent block (up to parent_chunk_size tokens) commonly spans
                    # more than one heading, so a child cut from deep inside it can
                    # legitimately start its own, later section. Always attributing
                    # every child to the *parent's* heading — the previous
                    # behaviour — means a child whose own text opens "## 5.1
                    # Required Documentation" still inherits "3.2 Pricing Floors"
                    # from wherever the parent block happened to start, because
                    # _extract_clause_ref checks its `heading` argument before
                    # `text`. The child's own heading, when it has one, must win.
                    child_heading = self._extract_heading(child_text) or parent_heading
                    clause_ref = self._extract_clause_ref(child_text, child_heading)
                    child_id = make_chunk_id(doc_name, f"p{pi}_c{ci}")
                    child_meta = ChunkMetadata(
                        **{
                            **base_metadata,
                            "clause_reference": clause_ref or parent_clause or f"p{pi}_c{ci}",
                            "section_heading": child_heading,
                            "parent_chunk_id": parent_id,
                        }
                    )
                    chunks.append(Chunk(id=child_id, text=child_text, metadata=child_meta))
        else:
            for i, child_text in enumerate(self._splitter.split_text(text)):
                child_text = _repair_fragmented_table(child_text, text)
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

        # Media chunks are built atomically from enriched elements — one figure /
        # table = exactly one chunk, never routed through the text splitter (so a
        # long caption can never be fragmented). See design §9.
        chunks.extend(
            self._build_media_chunks(extraction.elements, base_metadata, parent_id_by_heading)
        )

        logger.info("Chunking complete", total_chunks=len(chunks), document=doc_name)
        return chunks

    def _build_media_chunks(
        self,
        elements: list[ExtractedElement],
        base_metadata: dict[str, Any],
        parent_id_by_heading: dict[str, str],
    ) -> list[Chunk]:
        """Turn each enriched media element (those carrying an ``artifact_ref``)
        into exactly one atomic chunk. The full caption goes in as a single unit.
        """
        doc_name = base_metadata["document_name"]
        chunks: list[Chunk] = []
        for el in elements:
            ref = el.metadata.get("artifact_ref")
            if not ref:
                continue  # Not an enriched media element.
            if not el.text.strip():
                # artifact_ref is set unconditionally by the ingestion pipeline once
                # the image is stored, even when VLM enrichment failed — so this is
                # a stored-but-uncaptioned image, not an unenriched one. Dropping it
                # silently here is exactly the ORPHAN_ARTIFACT failure mode stage2a
                # detects after the fact; log it now, at the point it happens.
                logger.warning(
                    "Media element has no caption text; dropping from index "
                    "(image remains stored as an orphan artifact)",
                    artifact_ref=ref,
                    element_type=el.element_type.value,
                    page=el.page_number,
                    document=doc_name,
                )
                continue
            modality = el.element_type.value  # "figure" | "table"
            heading = el.metadata.get("nearest_heading", "")
            page = el.page_number
            clause_ref = (
                self._extract_clause_ref(el.text, heading)
                or (f"{modality} p.{page}" if page is not None else modality)
            )
            # Best-effort link to the parent section this figure/table visually
            # falls under, matched by heading text since media elements aren't
            # positioned inside any parent_text (they're built from raw elements,
            # never routed through the text splitter). Only resolves when the
            # element's nearest heading is itself the heading a parent chunk
            # starts on; a figure deep inside a section split into multiple
            # parent chunks links to that section's first parent rather than the
            # nearest one — an approximation, but real prose context beats none.
            parent_id = parent_id_by_heading.get(heading.strip().lower()) if heading else None
            meta = ChunkMetadata(
                **{
                    **base_metadata,
                    "section_heading": heading,
                    "clause_reference": clause_ref,
                    "source_page": page,
                    "modality": Modality(modality),
                    "artifact_ref": ref,
                    "bbox": el.bbox,
                    "enrichment_model": el.metadata.get("enrichment_model"),
                    "parent_chunk_id": parent_id,
                }
            )
            # Keyed on the artifact ref so re-ingesting maps the same image to the
            # same chunk id → idempotent upsert, same guarantee as text chunks.
            chunk_id = make_chunk_id(doc_name, f"{modality}:{ref}")
            chunks.append(Chunk(id=chunk_id, text=el.text, metadata=meta))
        return chunks

