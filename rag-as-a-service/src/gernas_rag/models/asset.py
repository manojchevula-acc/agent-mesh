"""Image asset domain models — mirrors the conventions in models/chunk.py."""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from .chunk import DocumentType


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Modality(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    IMAGE_STUB = "image_stub"  # A text chunk that DESCRIBES an image


class ContentType(str, Enum):
    """Chunk-level content classification (D8)."""

    TEXT = "text"
    TABLE = "table"
    LIST = "list"
    IMAGE_STUB = "image_stub"


class ImageRole(str, Enum):
    """Coarse classification, used for filtering and prompt phrasing."""

    FIGURE = "figure"
    CHART = "chart"
    TABLE_IMAGE = "table_image"
    DIAGRAM = "diagram"
    PAGE_RENDER = "page_render"
    UNKNOWN = "unknown"


class ImageAsset(BaseModel):
    """An image extracted from a source document.

    ``id`` is content-addressed (sha256 of the NORMALISED bytes), so the same
    figure appearing in two documents is stored once and re-ingestion is
    idempotent — mirroring the deterministic chunk-id contract in utils/hashing.py.
    """

    model_config = ConfigDict(frozen=True)

    id: str  # sha256[:32] of normalised bytes
    content_sha256: str  # Full digest (integrity / audit)
    phash: str  # 64-bit dHash, hex — near-duplicate detection

    # ── Provenance ───────────────────────────────────────────────────
    document_name: str
    document_type: DocumentType = DocumentType.OTHER
    page_number: int | None = None
    bbox: tuple[float, float, float, float] | None = None  # x0,y0,x1,y1 in PDF pts
    image_index_on_page: int = 0

    # ── Pixels ───────────────────────────────────────────────────────
    width: int
    height: int
    image_format: str = "WEBP"
    byte_size: int = 0
    role: ImageRole = ImageRole.UNKNOWN

    # ── Storage ──────────────────────────────────────────────────────
    uri: str  # Servable URI, e.g. /api/v1/assets/<id>
    storage_path: str  # Local FS path (never exposed to clients)
    thumbnail_uri: str | None = None

    # ── Textual context (feeds generation + image-stub chunks) ───────
    caption: str = ""
    surrounding_text: str = ""
    nearest_heading: str = ""
    ocr_text: str = ""
    parent_chunk_id: str | None = None  # Links back to the text collection

    # ── Filtering / freshness — MIRRORS ChunkMetadata so the same
    #     DocumentFilter can be applied to both collections ───────────
    product_applicability: list[str] = Field(default_factory=list)
    effective_date: str = ""
    last_indexed_at: datetime = Field(default_factory=_utcnow)
    freshness_score: float = 1.0
    deprecated: bool = False

    # ── Space identity — guards against cross-space contamination ────
    space_id: str = ""

    def descriptor(self, max_chars: int = 400) -> str:
        """Human/LLM-readable one-liner used for prompt injection and stubs."""
        parts = [p for p in (self.caption, self.nearest_heading, self.surrounding_text) if p]
        body = " · ".join(parts) or "(no caption available)"
        return body[:max_chars]


class EmbeddedImage(BaseModel):
    asset: ImageAsset
    dense_vector: list[float]
    space_id: str


class ImageIngestionResult(BaseModel):
    """Per-document outcome of the image sub-pipeline.

    ``images_indexed`` counts VECTORS in the image collection, which is not the
    same as "pictures in the document": a table contributes one crop vector in
    addition to its text chunk. ``figures`` and ``table_crops`` break it down.
    """

    images_indexed: int = 0
    figures: int = 0  # genuine pictures/charts/diagrams
    table_crops: int = 0  # rendered crops OF tables (D8 dual representation)
    stubs_created: int = 0
    stats: dict[str, int] = Field(default_factory=dict)
