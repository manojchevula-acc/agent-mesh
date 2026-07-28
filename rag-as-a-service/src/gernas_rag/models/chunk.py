"""Chunk domain models."""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DocumentType(str, Enum):
    PRICING_POLICY = "pricing_policy"
    REGULATORY = "regulatory"
    MRM = "mrm"
    PRODUCT_MANUAL = "product_manual"
    RISK_POLICY = "risk_policy"
    OTHER = "other"


class Modality(str, Enum):
    """Content modality of a chunk. TEXT is the default for every existing chunk."""

    TEXT = "text"
    TABLE = "table"
    FIGURE = "figure"
    PAGE_IMAGE = "page_image"


class ChunkMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_name: str
    document_type: DocumentType
    section_heading: str = ""
    clause_reference: str = ""
    product_applicability: list[str] = Field(default_factory=list)
    effective_date: str = ""
    last_indexed_at: datetime = Field(default_factory=_utcnow)
    freshness_score: float = 1.0
    deprecated: bool = False
    parent_chunk_id: str | None = None
    source_page: int | None = None

    # ── Multimodal image-as-text fields (default to a plain text chunk) ──
    modality: Modality = Modality.TEXT
    # Content-addressed key in the artifact store, e.g. "sha256:<hex>.png".
    artifact_ref: str | None = None
    # Region on the source page (x0, y0, x1, y1) for citation / future re-cropping.
    bbox: tuple[float, float, float, float] | None = None
    # VLM/version that produced the caption; None on fail-soft degrade.
    enrichment_model: str | None = None


class Chunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str  # Deterministic MD5 hash
    text: str
    metadata: ChunkMetadata
    is_parent: bool = False


class EmbeddedChunk(BaseModel):
    chunk: Chunk
    dense_vector: list[float]
    sparse_indices: list[int] = Field(default_factory=list)
    sparse_values: list[float] = Field(default_factory=list)
