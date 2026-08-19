"""Retrieval API request / response models."""

from pydantic import BaseModel, ConfigDict, Field


class DocumentFilter(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    document_type: list[str] | None = None
    product_applicability: list[str] | None = None
    effective_date_from: str | None = None
    deprecated: bool = False


class ImageQuery(BaseModel):
    """Image-as-query. Exactly one field must be set."""

    asset_id: str | None = None  # Re-query using an already-indexed asset
    image_base64: str | None = None
    # image_url is deliberately NOT supported: a server-side fetch of a
    # client-supplied URL is an SSRF primitive.


class RetrieveRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    query: str = Field(min_length=3, max_length=2000)
    filters: DocumentFilter = Field(default_factory=DocumentFilter)
    top_k: int = Field(default=5, ge=1, le=20)
    include_parent: bool = True
    generate_answer: bool = False  # If True, call LLM after retrieval

    # ── Multimodal (all optional — old payloads stay valid) ───────────
    include_images: bool | None = None  # None => decided by the intent router
    query_image: ImageQuery | None = None
    modalities: list[str] | None = None  # e.g. ["text"], ["text", "image"]


class RetrievedChunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    source: str  # document_name
    section_heading: str = ""
    clause_reference: str
    score: float
    effective_date: str
    freshness_warning: bool
    parent_text: str | None = None

    # ── Content classification (D8) ───────────────────────────────────
    content_type: str = "text"  # 'text' | 'table' | 'list' | 'image_stub'
    asset_id: str | None = None  # Set on table chunks with a rendered crop
    table_part: str | None = None  # "2/3" when the table was row-split
    page_number: int | None = None

    # ── Identity ────────────────────────────────────────────────────────
    # The deterministic MD5(doc_name::ref) chunk id — distinct from the
    # UUIDv5 Qdrant point id, and from `asset_id` above. Empty string when the
    # source didn't carry one (should not happen for live Qdrant results).
    chunk_id: str = ""
    is_parent: bool = False  # True if this hit is itself a parent-level chunk


class RetrievedImage(BaseModel):
    model_config = ConfigDict(frozen=True)

    asset_id: str
    uri: str
    thumbnail_uri: str | None = None
    source: str  # document_name
    page_number: int | None = None
    caption: str = ""
    nearest_heading: str = ""
    role: str = "unknown"
    score: float = 0.0  # Raw similarity in the multimodal space
    rank: int = 0
    width: int = 0
    height: int = 0
    effective_date: str = ""
    freshness_warning: bool = False
    # True when this image was surfaced because its table chunk was retrieved
    # (§8.7.6 step 5) rather than by the visual ANN.
    promoted_from_text: bool = False


class RetrieveResponse(BaseModel):
    chunks: list[RetrievedChunk]
    total_results: int
    latency_ms: float
    freshness_warning_global: bool
    answer: str | None = None  # Only populated if generate_answer=True
    cache_hit: bool = False

    # ── Multimodal (defaults keep old cached JSON valid) ──────────────
    images: list[RetrievedImage] = Field(default_factory=list)
    image_search_performed: bool = False
    multimodal_space_id: str | None = None
