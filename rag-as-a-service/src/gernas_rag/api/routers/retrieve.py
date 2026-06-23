"""POST /retrieve — hybrid retrieval with optional LLM answer generation."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from ...cache.redis_cache import RAGCache
from ...generation.generator import ResponseGenerator
from ...models.retrieval import RetrieveRequest, RetrieveResponse
from ...retrieval.pipeline import RetrievalPipeline
from ...utils.logging import get_logger
from ..deps import get_cache, get_generator, get_retrieval_pipeline, verify_auth

logger = get_logger(__name__)
router = APIRouter()


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(
    request: RetrieveRequest,
    background_tasks: BackgroundTasks,
    pipeline: RetrievalPipeline = Depends(get_retrieval_pipeline),
    generator: ResponseGenerator = Depends(get_generator),
    cache: RAGCache = Depends(get_cache),
    _: None = Depends(verify_auth),
) -> RetrieveResponse:
    """Retrieve relevant document chunks for a query.

    Optionally generate an LLM answer from the retrieved chunks. Results are cached
    in Redis for ``redis_cache_ttl_seconds``.
    """
    cache_key = cache.make_key(request)

    # Check cache
    cached = await cache.get(cache_key)
    if cached:
        response = RetrieveResponse.model_validate_json(cached)
        return response.model_copy(update={"cache_hit": True})

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
    return response
