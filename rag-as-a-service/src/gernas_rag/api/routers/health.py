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
