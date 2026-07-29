"""GET/PATCH/DELETE /search-history — browse and manage past /retrieve calls."""

from fastapi import APIRouter, Depends, HTTPException, status

from ...models.search_history import (
    RenameSearchHistoryRequest,
    SearchHistoryEntry,
    SearchHistoryListResponse,
)
from ...storage.search_history_store import BaseSearchHistoryStore
from ..deps import get_search_history_store, verify_auth

router = APIRouter()


@router.get("/search-history", response_model=SearchHistoryListResponse)
async def list_search_history(
    username: str,
    store: BaseSearchHistoryStore = Depends(get_search_history_store),
    _: None = Depends(verify_auth),
) -> SearchHistoryListResponse:
    """List a user's past searches, newest first, for the sidebar."""
    entries = await store.list(username)
    return SearchHistoryListResponse(entries=entries)


@router.get("/search-history/{search_id}", response_model=SearchHistoryEntry)
async def get_search_history_entry(
    search_id: str,
    username: str | None = None,
    store: BaseSearchHistoryStore = Depends(get_search_history_store),
    _: None = Depends(verify_auth),
) -> SearchHistoryEntry:
    """Return the full stored record (query, settings, response) for reopening."""
    entry = await store.get(search_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Search not found")
    if username and not store.check_owner(entry, username):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This search belongs to a different user",
        )
    return entry


@router.patch("/search-history/{search_id}")
async def rename_search_history_entry(
    search_id: str,
    body: RenameSearchHistoryRequest,
    store: BaseSearchHistoryStore = Depends(get_search_history_store),
    _: None = Depends(verify_auth),
) -> dict:
    """Set (or clear, if blank) a user-defined title — sidebar "rename"."""
    entry = await store.get(search_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Search not found")
    if not store.check_owner(entry, body.username):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This search belongs to a different user",
        )
    await store.rename(search_id, body.title)
    return {"search_id": search_id, "title": body.title.strip() or None}


@router.delete("/search-history/{search_id}")
async def delete_search_history_entry(
    search_id: str,
    username: str,
    store: BaseSearchHistoryStore = Depends(get_search_history_store),
    _: None = Depends(verify_auth),
) -> dict:
    """Delete a stored search entirely — sidebar "delete"."""
    entry = await store.get(search_id)
    if entry is not None and not store.check_owner(entry, username):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This search belongs to a different user",
        )
    await store.delete(search_id)
    return {"search_id": search_id, "deleted": True}
