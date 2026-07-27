"""Semantic response cache package.

Public surface:
    get_cache_store() -> SemanticCacheStore   (module-level lazy singleton)
    CacheEntry                                (dataclass returned by lookup)
"""
from src.cache.semantic_cache import CacheEntry, SemanticCacheStore, get_cache_store

__all__ = ["CacheEntry", "SemanticCacheStore", "get_cache_store"]
