"""FastAPI dependencies — pulled from ``app.state`` (the DI container)."""

from fastapi import Request

from ..cache.redis_cache import RAGCache
from ..config.settings import Settings
from ..generation.generator import ResponseGenerator
from ..ingestion.pipeline import IngestionPipeline
from ..retrieval.pipeline import RetrievalPipeline
from ..vectordb.base import BaseVectorDB
from .auth import verify_auth

__all__ = [
    "get_settings_dep",
    "get_retrieval_pipeline",
    "get_ingestion_pipeline",
    "get_generator",
    "get_cache",
    "get_vectordb",
    "verify_auth",
]


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_retrieval_pipeline(request: Request) -> RetrievalPipeline:
    return request.app.state.retrieval_pipeline


def get_ingestion_pipeline(request: Request) -> IngestionPipeline:
    return request.app.state.ingestion_pipeline


def get_generator(request: Request) -> ResponseGenerator:
    return request.app.state.generator


def get_cache(request: Request) -> RAGCache:
    return request.app.state.cache


def get_vectordb(request: Request) -> BaseVectorDB:
    return request.app.state.vectordb
