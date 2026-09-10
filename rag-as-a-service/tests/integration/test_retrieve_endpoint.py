"""Integration test: POST /api/v1/retrieve with fakes wired into app.state."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from gernas_rag.api.middleware import RequestIDMiddleware, StructuredLoggingMiddleware
from gernas_rag.api.routers import health, retrieve
from gernas_rag.cache.redis_cache import RAGCache
from gernas_rag.config.settings import Settings
from gernas_rag.generation.generator import ResponseGenerator
from gernas_rag.models.chunk import EmbeddedChunk
from gernas_rag.retrieval.pipeline import RetrievalPipeline
from gernas_rag.storage.search_history_store import LocalSearchHistoryStore


@pytest.fixture
def app(settings, fake_embedder, fake_vectordb, fake_llm, sample_chunk, tmp_path):
    settings = Settings(_env_file=None, redis_enabled=False, api_key=None)  # type: ignore[call-arg]
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(StructuredLoggingMiddleware)
    app.include_router(health.router, tags=["health"])
    app.include_router(retrieve.router, prefix="/api/v1", tags=["retrieval"])

    app.state.settings = settings
    app.state.embedder = fake_embedder
    app.state.vectordb = fake_vectordb
    app.state.llm = fake_llm
    app.state.cache = RAGCache(settings.redis_url, 900, enabled=False)
    app.state.retrieval_pipeline = RetrievalPipeline(settings, fake_embedder, fake_vectordb)
    app.state.generator = ResponseGenerator(settings, fake_llm)
    # The router records every search to history in a background task, so this has
    # to be present even though no test here asserts on it — deps.py resolves it
    # from app.state on the request path. Writing under tmp_path keeps each test's
    # history isolated and cleaned up.
    app.state.search_history_store = LocalSearchHistoryStore(tmp_path)
    return app


@pytest.fixture
async def seeded(fake_vectordb, sample_chunk):
    await fake_vectordb.upsert(
        [EmbeddedChunk(chunk=sample_chunk, dense_vector=[0.1] * 8, sparse_indices=[1], sparse_values=[0.5])]
    )
    return fake_vectordb


async def test_health_endpoint(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_retrieve_returns_chunks(app, seeded):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/retrieve",
            json={"query": "pricing floor BB-rated AED loan", "top_k": 5},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_results"] >= 1
    assert body["chunks"]
    assert body["cache_hit"] is False


async def test_retrieve_validation_error_on_short_query(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/retrieve", json={"query": "x"})
    assert resp.status_code == 422


async def test_retrieve_with_answer_generation(app, seeded):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/retrieve",
            json={"query": "what is the pricing floor", "top_k": 3, "generate_answer": True},
        )
    assert resp.status_code == 200
    assert resp.json()["answer"].startswith("FAKE-ANSWER")
