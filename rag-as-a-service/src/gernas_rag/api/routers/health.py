"""Health and readiness endpoints."""

from fastapi import APIRouter, Request

from ...utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.get("/health")
async def health() -> dict:
    """Liveness probe — always returns ok if the process is up."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> dict:
    """Readiness probe — verifies the vector DB is reachable."""
    vectordb = request.app.state.vectordb
    db_ok = await vectordb.health_check()
    status = "ready" if db_ok else "degraded"
    return {"status": status, "vectordb": db_ok}


@router.get("/multimodal/status")
async def multimodal_status(request: Request) -> dict:
    """Report which embedding space and collection are actually live.

    Essential for confirming, per environment, that the model in config is the
    model backing the index — the failure this design works hardest to prevent.
    """
    settings = request.app.state.settings
    embedder = getattr(request.app.state, "multimodal_embedder", None)
    image_store = getattr(request.app.state, "image_store", None)

    payload: dict = {
        "enabled": settings.multimodal.enabled,
        "retrieval_mode": settings.multimodal.retrieval.mode.value,
        "model_name": settings.multimodal.embedding.model_name,
        "vision_generation": {
            "enabled": settings.llm.vision_enabled,
            "model": settings.llm.vision_model_name if settings.llm.vision_enabled else None,
            "max_images": settings.llm.vision_max_images,
        },
        "table_protection": settings.chunking.protect_tables,
    }
    if embedder is not None:
        try:
            space = embedder.space
            payload["space_id"] = space.space_id
            payload["dim"] = space.dim
            payload["metric"] = space.metric
        except Exception as exc:  # noqa: BLE001 - status must never 500
            payload["space_error"] = str(exc)
    if image_store is not None:
        payload["image_collection"] = getattr(image_store, "collection_name", None)
        payload["image_count"] = await image_store.count()
        payload["image_store_healthy"] = await image_store.health_check()
    return payload
