"""Unit tests for the local search-history store."""

from datetime import datetime, timedelta, timezone

import pytest

from gernas_rag.models.retrieval import DocumentFilter, RetrieveResponse
from gernas_rag.models.search_history import SearchHistoryEntry
from gernas_rag.storage.search_history_store import LocalSearchHistoryStore


def _entry(search_id: str, username: str = "alice", created_at: datetime | None = None) -> SearchHistoryEntry:
    return SearchHistoryEntry(
        search_id=search_id,
        username=username,
        title=None,
        query="What is the pricing floor?",
        filters=DocumentFilter(),
        top_k=5,
        generate_answer=False,
        response=RetrieveResponse(
            chunks=[], total_results=0, latency_ms=1.0, freshness_warning_global=False
        ),
        created_at=created_at or datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_save_and_get_round_trip(tmp_path):
    store = LocalSearchHistoryStore(tmp_path)
    entry = _entry("s1")
    await store.save(entry)
    got = await store.get("s1")
    assert got is not None
    assert got.search_id == "s1"
    assert got.query == entry.query


@pytest.mark.asyncio
async def test_get_missing_returns_none(tmp_path):
    store = LocalSearchHistoryStore(tmp_path)
    assert await store.get("nope") is None


@pytest.mark.asyncio
async def test_get_corrupt_file_returns_none(tmp_path):
    store = LocalSearchHistoryStore(tmp_path)
    (tmp_path / "bad.json").write_text("{not valid json", encoding="utf-8")
    assert await store.get("bad") is None


@pytest.mark.asyncio
async def test_list_filters_by_username(tmp_path):
    store = LocalSearchHistoryStore(tmp_path)
    await store.save(_entry("s1", username="alice"))
    await store.save(_entry("s2", username="bob"))
    alice_results = await store.list("alice")
    assert [s.search_id for s in alice_results] == ["s1"]


@pytest.mark.asyncio
async def test_list_sorts_newest_first_and_respects_limit(tmp_path):
    store = LocalSearchHistoryStore(tmp_path)
    now = datetime.now(timezone.utc)
    await store.save(_entry("older", created_at=now - timedelta(minutes=5)))
    await store.save(_entry("newer", created_at=now))
    results = await store.list("alice")
    assert [s.search_id for s in results] == ["newer", "older"]
    assert await store.list("alice", limit=1) == results[:1]


@pytest.mark.asyncio
async def test_rename_updates_title(tmp_path):
    store = LocalSearchHistoryStore(tmp_path)
    await store.save(_entry("s1"))
    await store.rename("s1", "BB floor")
    got = await store.get("s1")
    assert got.title == "BB floor"


@pytest.mark.asyncio
async def test_rename_blank_clears_title(tmp_path):
    store = LocalSearchHistoryStore(tmp_path)
    await store.save(_entry("s1"))
    await store.rename("s1", "A title")
    await store.rename("s1", "   ")
    got = await store.get("s1")
    assert got.title is None


@pytest.mark.asyncio
async def test_rename_missing_entry_is_noop(tmp_path):
    store = LocalSearchHistoryStore(tmp_path)
    await store.rename("nope", "title")  # must not raise
    assert await store.get("nope") is None


@pytest.mark.asyncio
async def test_delete_removes_file(tmp_path):
    store = LocalSearchHistoryStore(tmp_path)
    await store.save(_entry("s1"))
    await store.delete("s1")
    assert await store.get("s1") is None


@pytest.mark.asyncio
async def test_delete_missing_is_noop(tmp_path):
    store = LocalSearchHistoryStore(tmp_path)
    await store.delete("nope")  # must not raise


def test_check_owner_permissive_when_no_owner_recorded(tmp_path):
    store = LocalSearchHistoryStore(tmp_path)
    entry = _entry("s1", username="")
    assert store.check_owner(entry, "anyone") is True


def test_check_owner_denies_mismatched_user(tmp_path):
    store = LocalSearchHistoryStore(tmp_path)
    entry = _entry("s1", username="alice")
    assert store.check_owner(entry, "bob") is False
    assert store.check_owner(entry, "alice") is True
