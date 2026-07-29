"""POST /retrieve — hybrid retrieval with optional LLM answer generation."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status

from ...cache.redis_cache import RAGCache
from ...generation.generator import ResponseGenerator
from ...models.retrieval import RetrieveRequest, RetrieveResponse
from ...models.search_history import SearchHistoryEntry
from ...retrieval.pipeline import RetrievalPipeline
from ...storage.search_history_store import BaseSearchHistoryStore
from ...utils.logging import get_logger
from ..deps import (
    get_cache,
    get_generator,
    get_retrieval_pipeline,
    get_search_history_store,
    verify_auth,
)

logger = get_logger(__name__)
router = APIRouter()


def _save_history(
    background_tasks: BackgroundTasks,
    store: BaseSearchHistoryStore,
    request: RetrieveRequest,
    response: RetrieveResponse,
    search_id: str,
    username: str | None,
) -> None:
    """Persist one search-history record. No-op if no identity was supplied.

    Identity travels via the ``X-Username`` header rather than a
    ``RetrieveRequest`` field deliberately — ``RAGCache.make_key()`` hashes the
    full request body, so a username field there would silently stop different
    users from ever sharing a cache hit on an identical question.
    """
    if not username:
        return
    entry = SearchHistoryEntry(
        search_id=search_id,
        username=username,
        title=None,
        query=request.query,
        filters=request.filters,
        top_k=request.top_k,
        generate_answer=request.generate_answer,
        response=response,
        created_at=datetime.now(timezone.utc),
    )
    background_tasks.add_task(store.save, entry)


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(
    request: RetrieveRequest,
    background_tasks: BackgroundTasks,
    pipeline: RetrievalPipeline = Depends(get_retrieval_pipeline),
    generator: ResponseGenerator = Depends(get_generator),
    cache: RAGCache = Depends(get_cache),
    history_store: BaseSearchHistoryStore = Depends(get_search_history_store),
    x_username: str | None = Header(default=None, alias="X-Username"),
    _: None = Depends(verify_auth),
) -> RetrieveResponse:
    """Retrieve relevant document chunks for a query.

    Optionally generate an LLM answer from the retrieved chunks. Results are cached
    in Redis for ``redis_cache_ttl_seconds``. Every call (cache hit or not) is
    persisted as one search-history entry, keyed off ``X-Username`` — a fresh
    user interaction each time, regardless of whether the underlying compute
    was served from cache.
    """
    cache_key = cache.make_key(request)
    search_id = str(uuid.uuid4())

    # Check cache
    cached = await cache.get(cache_key)
    if cached:
        response = RetrieveResponse.model_validate_json(cached)
        response = response.model_copy(update={"cache_hit": True, "search_id": search_id})
        _save_history(background_tasks, history_store, request, response, search_id, x_username)
        return response

    # Retrieve
    try:
        response = await pipeline.retrieve(request)

        # Optionally generate an answer
        if request.generate_answer and response.chunks:
            answer = await generator.generate(request.query, response.chunks)
            response = response.model_copy(update={"answer": answer})
    except Exception as exc:
        logger.error("Retrieval failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Retrieval failed",
        ) from exc

    # Cache in background
    background_tasks.add_task(cache.set, cache_key, response.model_dump_json())
    response = response.model_copy(update={"search_id": search_id})
    _save_history(background_tasks, history_store, request, response, search_id, x_username)
    return response
