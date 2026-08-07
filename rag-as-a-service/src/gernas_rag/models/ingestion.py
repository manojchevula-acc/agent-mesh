"""Ingestion domain models."""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class IngestionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"


class IngestionResult(BaseModel):
    file_path: str
    chunks_created: int = 0
    # Vectors in the image collection = figures + table_crops. NOT the number of
    # pictures in the document: each table adds a crop vector on top of its text
    # chunk (D8 dual representation).
    images_indexed: int = 0
    figures_indexed: int = 0  # genuine pictures/charts/diagrams
    table_crops_indexed: int = 0  # rendered crops OF tables
    tables_found: int = 0  # Atomic table chunks produced (D8)
    status: str = IngestionStatus.SUCCESS.value
    error: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class IngestionJob(BaseModel):
    job_id: str
    status: IngestionStatus = IngestionStatus.PENDING
    result: IngestionResult | None = None
    created_at: datetime = Field(default_factory=_utcnow)
