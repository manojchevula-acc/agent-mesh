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
    status: str = IngestionStatus.SUCCESS.value
    error: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class IngestionJob(BaseModel):
    job_id: str
    status: IngestionStatus = IngestionStatus.PENDING
    result: IngestionResult | None = None
    created_at: datetime = Field(default_factory=_utcnow)
