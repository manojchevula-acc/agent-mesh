"""Deterministic chunk ID generation."""

import hashlib
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
