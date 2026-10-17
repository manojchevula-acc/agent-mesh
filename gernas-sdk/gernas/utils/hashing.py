"""Payload fingerprints and sizes — the only things recorded about tool / model payloads."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _canonical(data: Any) -> str:
    try:
        return json.dumps(data, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(data)


def fingerprint(data: Any) -> str:
    """SHA-256 of the canonical JSON form of *data* (used internally for loop detection)."""
    return hashlib.sha256(_canonical(data).encode("utf-8", "replace")).hexdigest()


def payload_size(data: Any) -> int:
    """UTF-8 size in bytes of the canonical JSON form of *data* (Blueprint §3 tool payload size)."""
    if data is None:
        return 0
    if isinstance(data, (bytes, bytearray)):
        return len(data)
    if isinstance(data, str):
        return len(data.encode("utf-8", "replace"))
    return len(_canonical(data).encode("utf-8", "replace"))
