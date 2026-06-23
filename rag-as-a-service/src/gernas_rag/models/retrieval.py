"""Retrieval API request / response models."""

from pydantic import BaseModel, ConfigDict, Field


class DocumentFilter(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    document_type: list[str] | None = None
    product_applicability: list[str] | None = None
    effective_date_from: str | None = None
    deprecated: bool = False


class RetrieveRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    query: str = Field(min_length=3, max_length=2000)
    filters: DocumentFilter = Field(default_factory=DocumentFilter)
    top_k: int = Field(default=5, ge=1, le=20)
    include_parent: bool = True
    generate_answer: bool = False  # If True, call LLM after retrieval


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


class RetrieveResponse(BaseModel):
    chunks: list[RetrievedChunk]
    total_results: int
    latency_ms: float
    freshness_warning_global: bool
    answer: str | None = None  # Only populated if generate_answer=True
    cache_hit: bool = False
