"""FastAPI app factory + lifespan."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.middleware import RequestIDMiddleware, StructuredLoggingMiddleware
from .api.routers import admin, evaluate, health, ingest, retrieve
from .cache.redis_cache import RAGCache
from .config.settings import get_settings
from .embeddings.factory import get_embedder
from .generation.generator import ResponseGenerator
from .ingestion.pipeline import IngestionPipeline
from .llm.factory import get_llm
from .retrieval.pipeline import RetrievalPipeline
from .utils.logging import configure_logging, get_logger
from .utils.telemetry import configure_telemetry, instrument_fastapi
from .vectordb.factory import get_vectordb

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ────────────────────────────────────────────────────────
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_telemetry(settings.service_name)
    logger.info("Starting GERNAS RAG service", environment=settings.environment)

    embedder = get_embedder(settings.embedding)
    vectordb = get_vectordb(settings.vectordb)
    llm = get_llm(settings.llm)
    cache = RAGCache(
        settings.redis_url, settings.redis_cache_ttl_seconds, enabled=settings.redis_enabled
    )

    # Ensure the collection exists.
    await vectordb.create_collection(settings.vectordb.collection_name, embedder.dense_dim)

    # Store in app state — accessed in deps.py.
    app.state.settings = settings
    app.state.embedder = embedder
    app.state.vectordb = vectordb
    app.state.llm = llm
    app.state.cache = cache
    app.state.ingestion_pipeline = IngestionPipeline(settings, embedder, vectordb)
    app.state.retrieval_pipeline = RetrievalPipeline(settings, embedder, vectordb)
    app.state.generator = ResponseGenerator(settings, llm)

    yield

    # ── Shutdown ───────────────────────────────────────────────────────
    await cache.close()
    logger.info("GERNAS RAG service stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title="GERNAS RAG Service",
        version=settings.service_version,
        lifespan=lifespan,
        docs_url="/docs" if settings.debug else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(StructuredLoggingMiddleware)

    app.include_router(health.router, tags=["health"])
    app.include_router(retrieve.router, prefix="/api/v1", tags=["retrieval"])
    app.include_router(ingest.router, prefix="/api/v1", tags=["ingestion"])
    app.include_router(admin.router, prefix="/api/v1/admin", tags=["admin"])
    app.include_router(evaluate.router, prefix="/api/v1", tags=["evaluation"])

    instrument_fastapi(app)
    return app


app = create_app()
