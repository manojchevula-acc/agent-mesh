"""Deterministic chunk / asset / embedding-space ID generation."""

import hashlib
import re
import uuid

# Stable namespace so chunk UUIDs are reproducible across runs.
_NAMESPACE = uuid.UUID("6f1d2c3a-4b5e-4d7a-9c8b-0a1b2c3d4e5f")


def make_chunk_id(doc_name: str, ref: str) -> str:
    """Deterministic MD5-based hex id for a chunk.

    Used by chunkers so re-ingesting the same document overwrites (upserts) the
    same point ids — making ingestion idempotent.
    """
    return hashlib.md5(f"{doc_name}::{ref}".encode()).hexdigest()


def make_point_uuid(chunk_id: str) -> str:
    """Convert a deterministic chunk id into a deterministic UUID string.

    Some vector DBs (Qdrant) require point ids to be UUIDs or unsigned ints. A
    UUIDv5 over the chunk id keeps it deterministic while satisfying that
    constraint.
    """
    return str(uuid.uuid5(_NAMESPACE, chunk_id))


def make_asset_id(image_bytes: bytes) -> str:
    """Content-addressed asset id: first 32 hex chars of sha256.

    Deduplicates identical figures across documents and makes re-ingestion
    idempotent, matching the contract of :func:`make_chunk_id`.
    """
    return hashlib.sha256(image_bytes).hexdigest()[:32]


def make_space_id(
    provider: str,
    model_name: str,
    revision: str | None,
    dim: int,
    normalize: bool,
    metric: str,
) -> str:
    """Stable identity for an embedding space.

    Any change to the tuple yields a different id -> a different collection name
    -> no possibility of querying an index with an incompatible encoder.
    """
    raw = f"{provider}|{model_name}|{revision or 'main'}|{dim}|{int(normalize)}|{metric}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def slugify_model(model_name: str) -> str:
    """``google/siglip2-base-patch16-224`` -> ``siglip2_base_patch16_224``."""
    tail = model_name.rsplit("/", 1)[-1]
    return re.sub(r"[^a-zA-Z0-9]+", "_", tail).strip("_").lower()
