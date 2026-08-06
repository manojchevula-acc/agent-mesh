"""GET /assets/{asset_id} — serve stored images.

Auth is REQUIRED: assets are extracted from internal policy documents and may
contain confidential or personal data. They must not be publicly readable merely
because the id is unguessable.
"""

from fastapi import APIRouter, Depends, HTTPException, Response, status

from ...images.store import BaseAssetStore
from ...utils.logging import get_logger
from ..deps import get_asset_store, verify_auth

logger = get_logger(__name__)
router = APIRouter()

_MIME = {"webp": "image/webp", "png": "image/png", "jpeg": "image/jpeg", "jpg": "image/jpeg"}


def _media_type(store: BaseAssetStore) -> str:
    ext = getattr(store, "_ext", "webp")
    return _MIME.get(ext, "application/octet-stream")


@router.get("/assets/{asset_id}")
async def get_asset(
    asset_id: str,
    store: BaseAssetStore = Depends(get_asset_store),
    _: None = Depends(verify_auth),
) -> Response:
    try:
        data = store.get(asset_id)  # Validates the id shape internally.
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid asset id") from exc
    except (FileNotFoundError, OSError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Asset not found") from exc
    return Response(
        content=data,
        media_type=_media_type(store),
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/assets/{asset_id}/thumb")
async def get_thumbnail(
    asset_id: str,
    store: BaseAssetStore = Depends(get_asset_store),
    _: None = Depends(verify_auth),
) -> Response:
    try:
        data = store.get_thumbnail(asset_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid asset id") from exc
    except (FileNotFoundError, OSError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Asset not found") from exc
    return Response(
        content=data,
        media_type=_media_type(store),
        headers={"Cache-Control": "private, max-age=3600"},
    )
