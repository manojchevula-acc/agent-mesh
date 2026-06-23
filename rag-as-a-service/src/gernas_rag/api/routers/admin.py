"""Admin endpoints — reindex and collection management."""

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ...utils.logging import get_logger
from ..deps import get_vectordb, verify_auth

logger = get_logger(__name__)
router = APIRouter()


@router.post("/reindex")
async def reindex(
    request: Request,
    _: None = Depends(verify_auth),
) -> dict:
    """Recreate the collection (drop + create). Existing data is removed."""
    settings = request.app.state.settings
    vectordb = request.app.state.vectordb
    embedder = request.app.state.embedder
    name = settings.vectordb.collection_name
    try:
        await vectordb.delete_collection(name)
    except Exception as exc:  # Collection may not exist yet.
        logger.warning("Delete during reindex skipped", error=str(exc))
    await vectordb.create_collection(name, embedder.dense_dim)
    logger.info("Reindex complete", collection=name)
    return {"status": "reindexed", "collection": name}


@router.delete("/collection")
async def delete_collection(
    request: Request,
    vectordb=Depends(get_vectordb),
    _: None = Depends(verify_auth),
) -> dict:
    """Delete the configured collection."""
    settings = request.app.state.settings
    name = settings.vectordb.collection_name
    try:
        await vectordb.delete_collection(name)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete collection: {exc}",
        ) from exc
    return {"status": "deleted", "collection": name}
