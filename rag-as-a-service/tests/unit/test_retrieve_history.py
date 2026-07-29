"""Tests for the /retrieve router's search-history persistence.

Calls the ``retrieve`` route function directly (no ASGI/TestClient layer —
this repo has no router-level HTTP tests yet, and standing up the full app
would mean mocking its heavy lifespan model loading for no extra coverage).
"""

from fastapi import BackgroundTasks

import pytest

from gernas_rag.api.routers.retrieve import retrieve
from gernas_rag.cache.redis_cache import RAGCache
from gernas_rag.generation.generator import ResponseGenerator
from gernas_rag.models.retrieval import RetrieveRequest
from gernas_rag.retrieval.pipeline import RetrievalPipeline
from gernas_rag.storage.search_history_store import LocalSearchHistoryStore


class FakeCache:
    """In-memory stand-in for RAGCache — same three-method surface."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.set_calls = 0

    @staticmethod
    def make_key(request: RetrieveRequest) -> str:
        return RAGCache.make_key(request)

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str) -> None:
        self.store[key] = value
        self.set_calls += 1


@pytest.mark.asyncio
async def test_two_usernames_share_one_cache_key(settings, fake_embedder, fake_vectordb, fake_llm, tmp_path):
    """Regression guard: identity must travel via X-Username, never a
    RetrieveRequest field — RAGCache.make_key hashes the full request body, so
    a username field there would stop different users from ever sharing a hit."""
    pipeline = RetrievalPipeline(settings, fake_embedder, fake_vectordb)
    generator = ResponseGenerator(settings, fake_llm)
    cache = FakeCache()
    history_store = LocalSearchHistoryStore(tmp_path)
    request = RetrieveRequest(query="What is the pricing floor?", top_k=5, generate_answer=False)

    bg1 = BackgroundTasks()
    response1 = await retrieve(
        request=request,
        background_tasks=bg1,
        pipeline=pipeline,
        generator=generator,
        cache=cache,
        history_store=history_store,
        x_username="alice",
    )
    await bg1()  # run queued cache.set + history save

    bg2 = BackgroundTasks()
    response2 = await retrieve(
        request=request,
        background_tasks=bg2,
        pipeline=pipeline,
        generator=generator,
        cache=cache,
        history_store=history_store,
        x_username="bob",
    )
    await bg2()

    assert response1.cache_hit is False
    assert response2.cache_hit is True  # same key hit despite a different username
    assert cache.set_calls == 1  # only ever written once — proves the shared key

    # Each user still gets their own history entry, with distinct search_ids.
    assert response1.search_id != response2.search_id
    alice_entries = await history_store.list("alice")
    bob_entries = await history_store.list("bob")
    assert [e.search_id for e in alice_entries] == [response1.search_id]
    assert [e.search_id for e in bob_entries] == [response2.search_id]


@pytest.mark.asyncio
async def test_generate_answer_false_still_persists_with_none_answer(
    settings, fake_embedder, fake_vectordb, fake_llm, tmp_path
):
    pipeline = RetrievalPipeline(settings, fake_embedder, fake_vectordb)
    generator = ResponseGenerator(settings, fake_llm)
    cache = FakeCache()
    history_store = LocalSearchHistoryStore(tmp_path)
    request = RetrieveRequest(query="What is the pricing floor?", top_k=5, generate_answer=False)

    bg = BackgroundTasks()
    response = await retrieve(
        request=request,
        background_tasks=bg,
        pipeline=pipeline,
        generator=generator,
        cache=cache,
        history_store=history_store,
        x_username="alice",
    )
    await bg()

    entry = await history_store.get(response.search_id)
    assert entry is not None
    assert entry.response.answer is None


@pytest.mark.asyncio
async def test_no_username_header_skips_history(settings, fake_embedder, fake_vectordb, fake_llm, tmp_path):
    pipeline = RetrievalPipeline(settings, fake_embedder, fake_vectordb)
    generator = ResponseGenerator(settings, fake_llm)
    cache = FakeCache()
    history_store = LocalSearchHistoryStore(tmp_path)
    request = RetrieveRequest(query="What is the pricing floor?", top_k=5, generate_answer=False)

    bg = BackgroundTasks()
    response = await retrieve(
        request=request,
        background_tasks=bg,
        pipeline=pipeline,
        generator=generator,
        cache=cache,
        history_store=history_store,
        x_username=None,
    )
    await bg()

    assert response.search_id is not None  # still generated, just not persisted
    assert list(tmp_path.iterdir()) == []
