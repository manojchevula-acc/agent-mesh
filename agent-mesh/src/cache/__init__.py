"""Semantic response cache package.

Public surface:
    get_cache_store() -> SemanticCacheStore   (module-level lazy singleton)
    CacheEntry                                (dataclass returned by lookup)
    llm_cache_judge()                         (async gray-zone LLM judge)
"""
from src.cache.semantic_cache import CacheEntry, SemanticCacheStore, get_cache_store
from src.cache.cache_judge import llm_cache_judge
from src.cache.entity_extractor import (
    extract_entities,
    extract_entities_sync,
    extract_entities_batch_sync,
    canonicalize_query,
    signatures_match,
    signature_to_str,
    signature_from_str,
    EMPTY_SIGNATURE,
)
from src.cache.negative_filter import is_negative_answer

__all__ = [
    "CacheEntry",
    "SemanticCacheStore",
    "get_cache_store",
    "llm_cache_judge",
    "extract_entities",
    "extract_entities_sync",
    "extract_entities_batch_sync",
    "canonicalize_query",
    "is_negative_answer",
    "signatures_match",
    "signature_to_str",
    "signature_from_str",
    "EMPTY_SIGNATURE",
]
