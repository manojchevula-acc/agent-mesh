"""Document domain models."""

from pydantic import BaseModel, Field

from .chunk import DocumentType

__all__ = ["Document", "DocumentType"]


class Document(BaseModel):
    """A source document submitted for ingestion."""

    document_name: str
    document_type: DocumentType = DocumentType.OTHER
    file_path: str
    product_applicability: list[str] = Field(default_factory=list)
    effective_date: str = ""
    page_count: int = 0
