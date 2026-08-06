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

    # ── Multimodal / table additions ──────────────────────────────────
    # All default to today's values, so points written before this change
    # deserialise unchanged (Pydantic fills the defaults). No backfill needed.
    modality: str = "text"  # 'text' | 'image_stub' (Modality enum value)
    asset_id: str | None = None  # Image stubs, and table chunks with a crop
    content_type: str = "text"  # 'text' | 'table' | 'list' | 'image_stub'
    table_rows: int | None = None  # Row count — lets the generator size the block
    table_part: str | None = None  # "2/3" when an oversized table was row-split


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
