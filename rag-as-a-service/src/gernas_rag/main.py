"""FastAPI app factory + lifespan."""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.middleware import RequestIDMiddleware, StructuredLoggingMiddleware
from .api.routers import admin, assets, evaluate, health, ingest, retrieve
from .cache.redis_cache import RAGCache
from .config.settings import Settings, get_settings
from .embeddings.factory import get_embedder
from .generation.generator import ResponseGenerator
from .ingestion.pipeline import IngestionPipeline
from .llm.factory import get_llm
from .retrieval.multimodal_pipeline import MultimodalRetrievalPipeline
from .retrieval.pipeline import RetrievalPipeline
from .utils.logging import configure_logging, get_logger
from .utils.telemetry import configure_telemetry, instrument_fastapi
from .vectordb.factory import get_vectordb

logger = get_logger(__name__)


def _cache_namespace(settings: Settings, space_id: str | None) -> str:
    """Cache keys must depend on retrieval CONFIG, not just the request.

    Otherwise flipping multimodal.enabled serves stale image-free responses
    until the TTL expires, and a rollback serves responses containing images.
    """
    mm = settings.multimodal
    return (
        f"{int(mm.enabled)}:{mm.retrieval.mode.value}:{space_id or '-'}:"
        f"{int(settings.llm.vision_enabled)}"
    )


async def _build_multimodal(settings: Settings, text_embedder, vectordb):
    """Construct the multimodal components. Returns all-None when disabled."""
    if not settings.multimodal.enabled:
        return None, None, None, None, 0.10

    from .embeddings.multimodal.factory import get_multimodal_embedder, registry_score_floor
    from .images.store import get_asset_store
    from .ingestion.image_pipeline import ImageIngestionPipeline
    from .vectordb.image_factory import get_image_store

    embedder = get_multimodal_embedder(settings.multimodal.embedding)
    score_floor = registry_score_floor(settings.multimodal.embedding)

    if settings.multimodal.embedding.warmup_on_start:
        # Pay model load + first-forward now rather than on the first request.
        await asyncio.get_running_loop().run_in_executor(None, embedder.load)

    # Prefer the registry-declared dim so a lazy-load config keeps a fast boot;
    # the embedder asserts probed == declared on first real use.
    from .embeddings.base import EmbeddingSpace
    from .embeddings.multimodal.registry import resolve_spec
    from .utils.hashing import make_space_id

    spec = resolve_spec(
        settings.multimodal.embedding.model_name,
        settings.multimodal.embedding.provider.value
        if settings.multimodal.embedding.provider
        else None,
    )
    if embedder._space is not None or not spec.dim:  # type: ignore[attr-defined]
        space = embedder.space  # forces a load when the dim is unknown
    else:
        space = EmbeddingSpace(
            space_id=make_space_id(
                spec.provider,
                spec.hf_id,
                settings.multimodal.embedding.revision,
                spec.dim,
                settings.multimodal.embedding.normalize,
                settings.multimodal.embedding.distance_metric,
            ),
            provider=spec.provider,
            model_name=spec.hf_id,
            revision=settings.multimodal.embedding.revision,
            dim=spec.dim,
            metric=settings.multimodal.embedding.distance_metric,
            normalized=settings.multimodal.embedding.normalize,
            modalities=frozenset({"text", "image"}),
        )

    collection = settings.multimodal.image_collection_name or space.collection_name(
        settings.multimodal.image_collection_base
    )
    # Share the text collection's client — mandatory in embedded mode.
    image_store = get_image_store(settings.vectordb, collection, vectordb)
    await image_store.create_collection(collection, space.dim, space.metric)

    asset_store = get_asset_store(settings.multimodal.storage)
    image_pipeline = ImageIngestionPipeline(
        settings, embedder, text_embedder, image_store, vectordb, asset_store
    )

    logger.info(
        "Multimodal enabled",
        model=space.model_name,
        dim=space.dim,
        space_id=space.space_id,
        collection=collection,
        mode=settings.multimodal.retrieval.mode.value,
    )
    return embedder, image_store, asset_store, image_pipeline, score_floor


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

    # Ensure the text collection exists.
    await vectordb.create_collection(settings.vectordb.collection_name, embedder.dense_dim)

    # ── Multimodal (entirely flag-gated) ───────────────────────────────
    mm_embedder = image_store = asset_store = image_pipeline = None
    score_floor = 0.10
    try:
        (
            mm_embedder,
            image_store,
            asset_store,
            image_pipeline,
            score_floor,
        ) = await _build_multimodal(settings, embedder, vectordb)
    except Exception as exc:  # noqa: BLE001
        # A multimodal misconfiguration must not take down text retrieval.
        logger.error(
            "Multimodal initialisation failed; continuing text-only", error=str(exc)
        )

    space_id = None
    if mm_embedder is not None:
        try:
            space_id = mm_embedder.space.space_id
        except Exception:  # noqa: BLE001 - lazy load may defer this
            space_id = None

    cache = RAGCache(
        settings.redis_url,
        settings.redis_cache_ttl_seconds,
        enabled=settings.redis_enabled,
        key_namespace=_cache_namespace(settings, space_id),
    )

    payload_builder = None
    if settings.llm.vision_enabled and asset_store is not None:
        from .generation.image_payload import ImagePayloadBuilder

        payload_builder = ImagePayloadBuilder(asset_store, settings.llm)

    text_pipeline = RetrievalPipeline(settings, embedder, vectordb)

    # Store in app state — accessed in deps.py.
    app.state.settings = settings
    app.state.embedder = embedder
    app.state.multimodal_embedder = mm_embedder
    app.state.vectordb = vectordb
    app.state.image_store = image_store
    app.state.asset_store = asset_store
    app.state.llm = llm
    app.state.cache = cache
    app.state.ingestion_pipeline = IngestionPipeline(
        settings, embedder, vectordb, image_pipeline
    )
    app.state.retrieval_pipeline = text_pipeline
    app.state.multimodal_pipeline = MultimodalRetrievalPipeline(
        settings, text_pipeline, mm_embedder, image_store, score_floor
    )
    app.state.generator = ResponseGenerator(settings, llm, payload_builder)

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
    app.include_router(assets.router, prefix="/api/v1", tags=["assets"])
    app.include_router(admin.router, prefix="/api/v1/admin", tags=["admin"])
    app.include_router(evaluate.router, prefix="/api/v1", tags=["evaluation"])

    instrument_fastapi(app)
    return app


app = create_app()
