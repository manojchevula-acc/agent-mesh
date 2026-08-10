"""Ingestion pipeline configuration."""

from pydantic import BaseModel, Field


class IngestionConfig(BaseModel):
    docs_path: str = "./docs"  # Default directory of documents to ingest
    batch_size: int = 32  # Embedding batch size during ingestion
    max_concurrent_documents: int = 1  # Sequential on low-RAM machines; Docling OCR is memory-heavy
    supported_extensions: list[str] = Field(default_factory=lambda: [".pdf", ".docx"])
