"""Cross-encoder reranker for semantic-cache candidates (Phase 4, augment mode).

The dense bi-encoder (all-MiniLM-L6-v2) is fast but coarse: it embeds query and
document independently, so it can mis-rank near-duplicate candidates. A
**cross-encoder** scores the (query, candidate-query) pair jointly and is far
more accurate at ordering — at the cost of one forward pass per candidate.

Used in *augment* mode: after the entity gate, the reranker re-orders the
retrieved candidates and drops any whose score is below CACHE_RERANK_MIN_SCORE
(saving downstream LLM-judge calls on junk). The existing LLM judge still makes
the final gray-zone HIT/MISS decision on the reordered set.

Runs fully local (sentence-transformers, already installed) — no network, so it
is immune to the 429 / proxy-SSL failures that affect the remote LLM judge.
Model loads lazily; call warmup() at startup to move the cost off the first
request (mirrors SemanticCacheStore._warmup).
"""
from __future__ import annotations

import logging
import threading
from typing import List, Optional, Tuple

from src.config import Config

_log = logging.getLogger("agent_mesh.cache.reranker")

_model = None
_model_lock = threading.Lock()
_load_failed = False


def _get_model():
    """Lazy-load the CrossEncoder once (thread-safe). Returns None if unavailable."""
    global _model, _load_failed
    if _model is not None:
        return _model
    if _load_failed:
        return None
    with _model_lock:
        if _model is not None:
            return _model
        if _load_failed:
            return None
        try:
            from sentence_transformers import CrossEncoder
            _log.info("reranker: loading CrossEncoder %s ...", Config.CACHE_RERANKER_MODEL)
            _model = CrossEncoder(Config.CACHE_RERANKER_MODEL)
            _log.info("reranker: model ready")
        except Exception as exc:
            # Missing model/weights/torch → disable gracefully; caller keeps dense order.
            _load_failed = True
            _log.warning("reranker: load failed (%s) — reranking disabled", exc)
            return None
    return _model


def is_available() -> bool:
    """True when reranking is enabled and the model is (or can be) loaded."""
    if not Config.CACHE_RERANKER_ENABLED:
        return False
    return _get_model() is not None


def warmup() -> None:
    """Pre-load the model at startup (no-op when disabled)."""
    if not Config.CACHE_RERANKER_ENABLED:
        return
    _get_model()


def rerank(query: str, candidate_texts: List[str]) -> Optional[List[float]]:
    """Return a cross-encoder relevance score per candidate text (aligned order).

    Returns None if reranking is disabled/unavailable or on any error, so the
    caller falls back to the original dense ordering.
    """
    if not Config.CACHE_RERANKER_ENABLED or not candidate_texts:
        return None
    model = _get_model()
    if model is None:
        return None
    try:
        pairs = [(query, text or "") for text in candidate_texts]
        scores = model.predict(pairs)
        return [float(s) for s in scores]
    except Exception as exc:
        _log.warning("reranker: predict failed (%s) — keeping dense order", exc)
        return None


def rerank_entries(query: str, entries: list) -> Tuple[list, Optional[float]]:
    """Reorder CacheEntry-like objects by cross-encoder score and drop low scorers.

    Each entry must expose ``.query_original``. Returns (reordered_kept_entries,
    top_score). Entries scoring below CACHE_RERANK_MIN_SCORE are dropped. On any
    failure the original list is returned unchanged with top_score=None.
    """
    if not entries:
        return entries, None
    scores = rerank(query, [e.query_original for e in entries])
    if scores is None:
        return entries, None

    scored = list(zip(entries, scores))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    min_score = Config.CACHE_RERANK_MIN_SCORE
    kept = [e for e, s in scored if s >= min_score]
    # Never let the reranker empty a non-empty candidate set purely on the floor —
    # keep the single best so the LLM judge / gate can still weigh in.
    if not kept and scored:
        kept = [scored[0][0]]
    top_score = scored[0][1] if scored else None
    return kept, top_score
