"""Search history API models — one atomic record per /retrieve call.

A "search history entry" is not a multi-turn conversation: SearchPage is a
single-shot Q&A tool, so each entry captures exactly the request that produced
it (query, filters, settings) plus the full response it returned, so reopening
it later replays the original result rather than re-querying live.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from .retrieval import DocumentFilter, RetrieveResponse


class SearchHistoryEntry(BaseModel):
    """Full persisted record — the entire contents of one {search_id}.json file."""

    model_config = ConfigDict(populate_by_name=True)

    search_id: str
    username: str  # "" => legacy/unowned entry, permissive on ownership checks
    title: str | None = None  # user override; UI falls back to `query` when None
    query: str
    filters: DocumentFilter
    top_k: int
    generate_answer: bool
    response: RetrieveResponse
    created_at: datetime


class SearchHistorySummary(BaseModel):
    """Lightweight projection for the sidebar list — no chunks/answer payload."""

    search_id: str
    query: str
    title: str | None = None
    created_at: datetime
    top_k: int
    generate_answer: bool
    total_results: int
    cache_hit: bool


class SearchHistoryListResponse(BaseModel):
    entries: list[SearchHistorySummary]


class RenameSearchHistoryRequest(BaseModel):
    username: str
    title: str  # blank clears the custom title, falls back to query
