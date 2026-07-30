"""LLM-based entity extraction for entity-aware cache gating.

The semantic cache matches on *intent* (dense-vector cosine similarity). Two
queries that differ only by a discrete identifier — e.g. "show customer profile
for CUST001" vs "...CUST002" — are ~90% lexically identical and score above the
HIT threshold, so the cache would serve CUST001's answer for a CUST002 query.

This module extracts the *entities* a query is about (customer/account/deal IDs,
people, products, time scope, amounts, ...) into a normalized, order-independent
**signature** (a ``frozenset[str]``). The cache gate (CacheCheckExecutor) only
lets a cached candidate survive when its signature *exactly* matches the incoming
query's — so a different entity always forces a MISS.

Design notes
------------
* LLM-based so *all* entity kinds are covered without hand-enumerating patterns.
* Uses the same httpx raw-HTTP pattern + GROQ_API_KEY / LLM_BASE_URL config as
  src/cache/cache_judge.py — no new dependency.
* Graceful degradation: on timeout/error the LLM path falls back to a
  deterministic regex extractor for structured IDs, so the reported
  CUST001/CUST002 bug is caught even when the LLM is unavailable.
* Memoized in-process by normalized query text — a repeated query is extracted
  once. Stored candidates carry their signature in ChromaDB metadata, so a
  lookup adds at most one extraction call (the incoming query), not one per
  candidate.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import FrozenSet, List, Optional

import httpx

from src.config import Config

_log = logging.getLogger("agent_mesh.cache.entity")

# An EntitySignature is a canonical, order-independent set of "type:value" tokens.
EntitySignature = FrozenSet[str]

EMPTY_SIGNATURE: EntitySignature = frozenset()

# ---------------------------------------------------------------------------
# Deterministic regex fallback (structured IDs only)
# ---------------------------------------------------------------------------
# Mirrors the compiled-pattern style of src/guardrails/deterministic_filters.py.
# These cover the common structured identifiers in the FAB dataset so the gate
# still blocks a mismatched ID even if the LLM extractor is unavailable.
# The FAB dataset uses both "CUST001" and "CUST_007" (optional _/- separator).
# Bucket labels MUST match the LLM extractor's buckets (_LIST_BUCKETS) so the regex
# and LLM paths produce identical signature tokens for the same entity — otherwise
# a regex-ingested entry ("customer_ids:cust001") never matches an LLM lookup and the
# entity gate drops every candidate.
_ID_PATTERNS = {
    "customer_ids": re.compile(r"\bCUST[_-]?\d+\b", re.IGNORECASE),
    "accounts": re.compile(r"\bACC[_-]?\d+\b", re.IGNORECASE),
    "deals": re.compile(r"\bDEAL[_-]?\d+\b", re.IGNORECASE),
}


# Placeholders used by canonicalize_query (Phase 2 templating).
_CANON_PLACEHOLDERS = {
    "customer_ids": "<CUSTOMER_ID>",
    "accounts": "<ACCOUNT>",
    "deals": "<DEAL>",
}


def canonicalize_query(text: str) -> str:
    """Replace structured-ID entities with type placeholders for embedding.

    "show profile for CUST001" and "...CUST_007" both become
    "show profile for <CUSTOMER_ID>", so paraphrases of the same intent embed
    near-identically. Regex-only (deterministic, zero latency) — the entity gate
    remains the discriminator that keeps CUST001 and CUST_007 as distinct entries.
    """
    if not text:
        return text
    out = text
    for label, pattern in _ID_PATTERNS.items():
        out = pattern.sub(_CANON_PLACEHOLDERS[label], out)
    return out


def extract_entities_regex(text: str) -> EntitySignature:
    """Deterministic fallback: extract structured IDs into a signature.

    Zero-latency, no dependency, no network. Used when the LLM extractor fails
    and as a safety net so the CUST001/CUST002 case is always caught.
    """
    tokens: set[str] = set()
    for label, pattern in _ID_PATTERNS.items():
        for match in pattern.findall(text or ""):
            # Lowercase to match the LLM path's _normalize_value → case-insensitive
            # ID matching (CUST001 == cust001) and identical tokens across extractors.
            tokens.add(f"{label}:{match.lower()}")
    return frozenset(tokens)


# ---------------------------------------------------------------------------
# Signature normalization + comparison
# ---------------------------------------------------------------------------

# Buckets returned by the extractor LLM. "time_scope" is a scalar string; the
# rest are lists. Everything is normalized to lowercase "bucket:value" tokens.
_LIST_BUCKETS = ("customer_ids", "accounts", "deals", "people", "products", "amounts", "other")


def _signature_from_payload(payload: dict) -> EntitySignature:
    """Turn the extractor's JSON payload into a canonical signature set."""
    tokens: set[str] = set()
    if not isinstance(payload, dict):
        return EMPTY_SIGNATURE
    for bucket in _LIST_BUCKETS:
        values = payload.get(bucket) or []
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            continue
        for value in values:
            norm = _normalize_value(value)
            if norm:
                tokens.add(f"{bucket}:{norm}")
    time_scope = payload.get("time_scope")
    norm_time = _normalize_value(time_scope) if time_scope else ""
    if norm_time:
        tokens.add(f"time_scope:{norm_time}")
    return frozenset(tokens)


def _normalize_value(value) -> str:
    """Canonicalize a single entity value: str, trimmed, lowercased, ws-collapsed."""
    if value is None:
        return ""
    s = str(value).strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def signatures_match(a: EntitySignature, b: EntitySignature) -> bool:
    """Return True iff two entity signatures refer to the same entities.

    Exact set equality. Both-empty ⇒ match (entity-free queries like
    "list all customers" still cache normally). Any difference ⇒ no match,
    which the hard gate treats as a MISS.
    """
    return a == b


# ---------------------------------------------------------------------------
# LLM extraction
# ---------------------------------------------------------------------------

_EXTRACT_PROMPT = """\
You are an entity extractor for a financial services (bank) AI cache.
Extract every entity in the user query that would make two otherwise-identical
questions refer to DIFFERENT things (so a cached answer for one must NOT be
reused for another).

User query: "{query}"

Return ONLY a JSON object with these exact keys (use [] or "" when none):
{{"customer_ids": [], "accounts": [], "deals": [], "people": [],
  "products": [], "time_scope": "", "amounts": [], "other": []}}

Rules:
- customer_ids: identifiers like CUST001 (uppercase them).
- accounts / deals: account or deal identifiers.
- people: named individuals (e.g. "Alice").
- products: named products / instruments.
- time_scope: a period or date that scopes the answer (e.g. "last quarter", "2025", "recently"). One short string.
- amounts: specific monetary amounts or numeric filters.
- other: any other distinguishing entity.
- Do NOT include generic intent words (show, profile, credit score, list, get).
- Output JSON only. No prose, no code fences."""


# In-process memoization keyed by normalized query text.
_cache_lock = threading.Lock()
_signature_cache: dict[str, EntitySignature] = {}
_CACHE_MAX = 2048


def _cache_key(query: str) -> str:
    return re.sub(r"\s+", " ", (query or "").strip().lower())


def _cache_get(query: str) -> Optional[EntitySignature]:
    with _cache_lock:
        return _signature_cache.get(_cache_key(query))


def _cache_put(query: str, sig: EntitySignature) -> None:
    with _cache_lock:
        if len(_signature_cache) >= _CACHE_MAX:
            _signature_cache.clear()  # simple bounded reset — signatures are cheap to recompute
        _signature_cache[_cache_key(query)] = sig


def _parse_extractor_response(raw: str) -> EntitySignature:
    """Parse the extractor LLM's JSON response into a signature.

    Tolerates leading/trailing prose or code fences by extracting the first
    JSON object. On any parse failure returns EMPTY_SIGNATURE (caller falls back
    to regex).
    """
    if not raw:
        return EMPTY_SIGNATURE
    text = raw.strip()
    # Strip common code-fence wrappers.
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    # Grab the first {...} block if the model added prose around it.
    if not text.startswith("{"):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            text = match.group(0)
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return EMPTY_SIGNATURE
    return _signature_from_payload(payload)


def _build_payload(query: str) -> dict:
    return {
        "model": Config.CACHE_ENTITY_MODEL,
        "messages": [{"role": "user", "content": _EXTRACT_PROMPT.format(query=query)}],
        "max_tokens": 200,
        "temperature": 0,
    }


def _build_headers() -> dict:
    return {
        "Authorization": f"Bearer {Config.GROQ_API_KEY}",
        "Content-Type": "application/json",
    }


async def extract_entities(query: str, role: str = "") -> EntitySignature:
    """Return the normalized entity signature for a query (async).

    Memoized by normalized query text. On LLM timeout/error, degrades to the
    deterministic regex extractor so a structured-ID mismatch is still caught.
    """
    if not query:
        return EMPTY_SIGNATURE
    cached = _cache_get(query)
    if cached is not None:
        return cached

    if not Config.ENABLE_RESPONSE_CACHE:
        return EMPTY_SIGNATURE

    url = f"{Config.LLM_BASE_URL.rstrip('/')}/chat/completions"
    timeout = Config.CACHE_ENTITY_EXTRACT_TIMEOUT
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=_build_payload(query), headers=_build_headers())
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            sig = _parse_extractor_response(raw)
    except Exception as exc:
        _log.warning("entity extract error (falling back to regex): %s", exc)
        sig = extract_entities_regex(query)

    # Always union the deterministic regex IDs so structured identifiers are caught
    # even if the LLM missed/misclassified them — keeps store & lookup signatures aligned.
    sig = sig | extract_entities_regex(query)
    _cache_put(query, sig)
    return sig


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff (1, 2, 4, … capped at 30s) for retry attempt N."""
    return float(min(2 ** attempt, 30))


def _retry_after_seconds(resp: "httpx.Response") -> Optional[float]:
    """Parse a numeric Retry-After header (seconds); None if absent/unparseable."""
    val = resp.headers.get("Retry-After")
    if not val:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _chat_completion_sync(payload: dict, timeout: float) -> str:
    """POST to the chat-completions endpoint with retry/backoff on transient
    failures (HTTP 429 and connection/SSL errors). Returns the content string;
    raises on final failure so callers can fall back to regex.
    """
    url = f"{Config.LLM_BASE_URL.rstrip('/')}/chat/completions"
    max_retries = max(0, Config.CACHE_ENTITY_MAX_RETRIES)
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(url, json=payload, headers=_build_headers())
                if resp.status_code == 429 and attempt < max_retries:
                    wait = _retry_after_seconds(resp)
                    time.sleep(wait if wait is not None else _backoff_seconds(attempt))
                    continue
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:  # connection/SSL/timeout/HTTP error
            last_exc = exc
            if attempt < max_retries:
                time.sleep(_backoff_seconds(attempt))
                continue
            raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("unreachable")


def extract_entities_sync(query: str, role: str = "") -> EntitySignature:
    """Blocking variant for the ingest CLI / backfill (no running event loop).

    Same behavior as extract_entities() but uses a synchronous httpx client
    with retry/backoff, and falls back to regex on final failure.
    """
    if not query:
        return EMPTY_SIGNATURE
    cached = _cache_get(query)
    if cached is not None:
        return cached

    if not Config.ENABLE_RESPONSE_CACHE:
        return EMPTY_SIGNATURE

    try:
        raw = _chat_completion_sync(_build_payload(query), Config.CACHE_ENTITY_EXTRACT_TIMEOUT)
        sig = _parse_extractor_response(raw)
    except Exception as exc:
        _log.warning("entity extract (sync) error (falling back to regex): %s", exc)
        sig = extract_entities_regex(query)

    sig = sig | extract_entities_regex(query)  # always include deterministic IDs
    _cache_put(query, sig)
    return sig


# ---------------------------------------------------------------------------
# Batched extraction (bulk ingest — many queries per LLM call to avoid 429s)
# ---------------------------------------------------------------------------

_BATCH_PROMPT = """\
You are an entity extractor for a financial services (bank) AI cache.
For EACH numbered query below, extract every entity that would make two otherwise-identical
questions refer to DIFFERENT things: customer/account/deal IDs, named people, products,
a scoping time period/date, specific amounts, or any other distinguishing entity.
Ignore generic intent words (show, profile, credit score, list, get).

Queries:
{numbered_queries}

Return ONLY a JSON object mapping each query's number (as a string) to its entities:
{{"0": {{"customer_ids": [], "accounts": [], "deals": [], "people": [], "products": [], "time_scope": "", "amounts": [], "other": []}}, "1": {{...}}}}
Output JSON only. No prose, no code fences."""


def _build_batch_payload(queries: List[str]) -> dict:
    numbered = "\n".join(f"{i}. {q}" for i, q in enumerate(queries))
    return {
        "model": Config.CACHE_ENTITY_MODEL,
        "messages": [{"role": "user", "content": _BATCH_PROMPT.format(numbered_queries=numbered)}],
        "max_tokens": min(80 * len(queries) + 60, 4000),
        "temperature": 0,
    }


def _parse_batch_response(raw: str, count: int) -> List[Optional[EntitySignature]]:
    """Parse the batch JSON object into a list of signatures aligned by index.

    Returns a list of length `count`; entries the model omitted/garbled are None
    so the caller can regex-fallback just those.
    """
    out: List[Optional[EntitySignature]] = [None] * count
    if not raw:
        return out
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    if not text.startswith("{"):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            text = match.group(0)
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return out
    if not isinstance(obj, dict):
        return out
    for i in range(count):
        payload = obj.get(str(i))
        if isinstance(payload, dict):
            out[i] = _signature_from_payload(payload)
    return out


def extract_entities_batch_sync(queries: List[str]) -> List[EntitySignature]:
    """Extract signatures for many queries using few LLM calls (bulk ingest).

    Batches into groups of CACHE_ENTITY_BATCH_SIZE, retries on 429/transient
    errors, memoizes per query, and regex-falls-back any query the LLM omitted
    or when a whole batch fails. Returns signatures aligned to `queries` order.
    """
    results: List[Optional[EntitySignature]] = [None] * len(queries)

    # Serve memoized queries first; collect the misses (dedup by normalized key).
    misses: List[int] = []
    seen: dict[str, int] = {}
    for idx, q in enumerate(queries):
        if not q:
            results[idx] = EMPTY_SIGNATURE
            continue
        cached = _cache_get(q)
        if cached is not None:
            results[idx] = cached
            continue
        misses.append(idx)

    if not Config.ENABLE_RESPONSE_CACHE:
        return [r if r is not None else EMPTY_SIGNATURE for r in results]

    batch_size = max(1, Config.CACHE_ENTITY_BATCH_SIZE)
    # Larger timeout for batches (more tokens to generate).
    timeout = max(Config.CACHE_ENTITY_EXTRACT_TIMEOUT, 15.0)

    for start in range(0, len(misses), batch_size):
        chunk_idx = misses[start:start + batch_size]
        chunk_queries = [queries[i] for i in chunk_idx]
        try:
            raw = _chat_completion_sync(_build_batch_payload(chunk_queries), timeout)
            sigs = _parse_batch_response(raw, len(chunk_queries))
        except Exception as exc:
            _log.warning("entity batch extract error (falling back to regex for %d queries): %s",
                         len(chunk_queries), exc)
            sigs = [None] * len(chunk_queries)
        for j, i in enumerate(chunk_idx):
            sig = sigs[j] if sigs[j] is not None else extract_entities_regex(queries[i])
            sig = sig | extract_entities_regex(queries[i])  # always include deterministic IDs
            _cache_put(queries[i], sig)
            results[i] = sig

    return [r if r is not None else EMPTY_SIGNATURE for r in results]


# ---------------------------------------------------------------------------
# Signature <-> storable-string helpers (for ChromaDB metadata)
# ---------------------------------------------------------------------------

def signature_to_str(sig: EntitySignature) -> str:
    """Serialize a signature to a stable string for ChromaDB metadata."""
    return "|".join(sorted(sig))


def signature_from_str(s: Optional[str]) -> EntitySignature:
    """Deserialize a signature previously stored via signature_to_str().

    Returns None-sentinel semantics via a separate helper is avoided: callers
    that need to distinguish "no metadata present" from "empty signature" should
    check the raw metadata value before calling this.
    """
    if not s:
        return EMPTY_SIGNATURE
    return frozenset(tok for tok in s.split("|") if tok)
