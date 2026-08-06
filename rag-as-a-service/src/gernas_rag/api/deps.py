"""FastAPI dependencies — pulled from ``app.state`` (the DI container)."""

from fastapi import HTTPException, Request, status

from ..cache.redis_cache import RAGCache
from ..config.settings import Settings
from ..generation.generator import ResponseGenerator
from ..images.store import BaseAssetStore
from ..ingestion.pipeline import IngestionPipeline
from ..retrieval.multimodal_pipeline import MultimodalRetrievalPipeline
from ..retrieval.pipeline import RetrievalPipeline
from ..vectordb.base import BaseVectorDB
from ..vectordb.image_store import BaseImageStore
from .auth import verify_auth

__all__ = [
    "get_settings_dep",
    "get_retrieval_pipeline",
    "get_multimodal_pipeline",
    "get_ingestion_pipeline",
    "get_generator",
    "get_cache",
    "get_vectordb",
    "get_image_store",
    "get_asset_store",
    "verify_auth",
]


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_retrieval_pipeline(request: Request) -> RetrievalPipeline:
    return request.app.state.retrieval_pipeline


def get_multimodal_pipeline(request: Request) -> MultimodalRetrievalPipeline:
    """The retrieval entry point.

    With the flag off this object is a pass-through wrapper around the text
    pipeline, so routing through it changes nothing.
    """
    return request.app.state.multimodal_pipeline


def get_ingestion_pipeline(request: Request) -> IngestionPipeline:
    return request.app.state.ingestion_pipeline


def get_generator(request: Request) -> ResponseGenerator:
    return request.app.state.generator


def get_cache(request: Request) -> RAGCache:
    return request.app.state.cache


def get_vectordb(request: Request) -> BaseVectorDB:
    return request.app.state.vectordb


def get_image_store(request: Request) -> BaseImageStore:
    store = getattr(request.app.state, "image_store", None)
    if store is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Multimodal retrieval is not enabled"
        )
    return store


def get_asset_store(request: Request) -> BaseAssetStore:
    store = getattr(request.app.state, "asset_store", None)
    if store is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Image assets are not enabled"
        )
    return store
