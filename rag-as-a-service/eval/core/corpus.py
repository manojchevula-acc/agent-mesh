"""A read-only, fully-materialised view of the indexed corpus.

Several stages need to reason over *every* stored chunk (integrity checks,
qrels derivation, orphan-artifact detection), which the search-oriented
``BaseVectorDB`` interface deliberately does not expose. Rather than widen the
production interface for the evaluation suite's benefit, the scroll lives here
and is implemented per backend.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from gernas_rag.config.settings import Settings
from gernas_rag.config.vectordb import VectorDBProvider

MEDIA_MODALITIES = ("figure", "table", "page_image")


@dataclass(frozen=True)
class IndexedChunk:
    """One stored chunk, flattened from its Qdrant payload."""

    chunk_id: str
    text: str
    document: str
    modality: str
    clause_reference: str
    section_heading: str
    artifact_ref: str | None
    source_page: int | None
    bbox: tuple[float, float, float, float] | None
    enrichment_model: str | None
    parent_chunk_id: str | None
    effective_date: str
    is_parent: bool
    deprecated: bool
    payload: dict[str, Any] = field(repr=False, default_factory=dict)

    @property
    def is_media(self) -> bool:
        return self.modality in MEDIA_MODALITIES

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "IndexedChunk":
        bbox = payload.get("bbox")
        return cls(
            chunk_id=payload.get("chunk_id", ""),
            text=payload.get("text", ""),
            document=payload.get("document_name", ""),
            modality=payload.get("modality", "text") or "text",
            clause_reference=payload.get("clause_reference", "") or "",
            section_heading=payload.get("section_heading", "") or "",
            artifact_ref=payload.get("artifact_ref"),
            source_page=payload.get("source_page"),
            bbox=tuple(bbox) if isinstance(bbox, (list, tuple)) and len(bbox) == 4 else None,
            enrichment_model=payload.get("enrichment_model"),
            parent_chunk_id=payload.get("parent_chunk_id"),
            effective_date=payload.get("effective_date", "") or "",
            is_parent=bool(payload.get("is_parent", False)),
            deprecated=bool(payload.get("deprecated", False)),
            payload=payload,
        )


class ChunkIndex:
    """In-memory index over every chunk in the collection."""

    def __init__(self, chunks: list[IndexedChunk], collection: str = "") -> None:
        self.chunks = chunks
        self.collection = collection
        self.by_id: dict[str, IndexedChunk] = {}
        self.by_document: dict[str, list[IndexedChunk]] = defaultdict(list)
        self.by_modality: dict[str, list[IndexedChunk]] = defaultdict(list)
        self.by_artifact_ref: dict[str, list[IndexedChunk]] = defaultdict(list)
        self.duplicate_ids: list[str] = []

        for chunk in chunks:
            if chunk.chunk_id in self.by_id:
                self.duplicate_ids.append(chunk.chunk_id)
            else:
                self.by_id[chunk.chunk_id] = chunk
            self.by_document[chunk.document].append(chunk)
            self.by_modality[chunk.modality].append(chunk)
            if chunk.artifact_ref:
                self.by_artifact_ref[chunk.artifact_ref].append(chunk)

    # ── Convenience views ─────────────────────────────────────────────
    @property
    def media(self) -> list[IndexedChunk]:
        return [c for c in self.chunks if c.is_media]

    @property
    def text_chunks(self) -> list[IndexedChunk]:
        return [c for c in self.chunks if not c.is_media]

    @property
    def parents(self) -> list[IndexedChunk]:
        return [c for c in self.chunks if c.is_parent]

    @property
    def children(self) -> list[IndexedChunk]:
        return [c for c in self.chunks if not c.is_parent]

    @property
    def documents(self) -> list[str]:
        return sorted(d for d in self.by_document if d)

    def find(
        self,
        document: str,
        clause_reference: str | None = None,
        modality: str | None = None,
        include_parents: bool = False,
    ) -> list[IndexedChunk]:
        """Look up chunks by document, optionally narrowing by clause and modality.

        Clause matching is tolerant (exact, or one side a prefix of the other on a
        dot boundary) because ``clause_reference`` is derived text and is not
        normalised identically on every ingest. It is used *only* to bootstrap
        qrels for human review — never as the identity key during scoring, where
        ``chunk_id`` is used instead.
        """
        candidates = self.by_document.get(document, [])
        if not include_parents:
            candidates = [c for c in candidates if not c.is_parent]
        if modality:
            candidates = [c for c in candidates if c.modality == modality]
        if clause_reference is not None:
            candidates = [c for c in candidates if clause_matches(clause_reference, c.clause_reference)]
        return candidates

    @classmethod
    async def load(cls, settings: Settings) -> "ChunkIndex":
        provider = settings.vectordb.provider
        if provider != VectorDBProvider.QDRANT:
            raise NotImplementedError(
                f"ChunkIndex currently supports Qdrant only (configured: {provider.value}). "
                "Add a scroll implementation for this backend to run the integrity stages."
            )
        payloads = await _scroll_qdrant(settings)
        return cls(
            [IndexedChunk.from_payload(p) for p in payloads],
            collection=settings.vectordb.collection_name,
        )


def clause_matches(expected: str, actual: str) -> bool:
    """Tolerant clause comparison used only for bootstrapping, never for scoring.

    Exact match, or one side is a dot-boundary prefix of the other ("2.1" matches
    "2.1.1" but not "2.14"). The dot-boundary requirement is what stops the naive
    substring test from counting "2.1" as a hit against "12.14".
    """
    if not expected or not actual:
        return expected == actual
    a, b = expected.strip().casefold(), actual.strip().casefold()
    if a == b:
        return True
    for short, long in ((a, b), (b, a)):
        if long.startswith(short) and long[len(short):].startswith("."):
            return True
    return False


async def _scroll_qdrant(settings: Settings) -> list[dict[str, Any]]:
    from qdrant_client import AsyncQdrantClient

    config = settings.vectordb
    client = (
        AsyncQdrantClient(path=config.qdrant_path)
        if config.qdrant_path
        else AsyncQdrantClient(url=config.qdrant_url, api_key=config.qdrant_api_key)
    )
    try:
        payloads: list[dict[str, Any]] = []
        offset = None
        while True:
            batch, offset = await client.scroll(
                collection_name=config.collection_name,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            payloads.extend(point.payload or {} for point in batch)
            if offset is None:
                return payloads
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result
