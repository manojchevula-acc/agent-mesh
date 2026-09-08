"""Input hashing and output summarisation utilities.

Inputs/outputs are never stored raw in telemetry — only hashes and structural summaries.
"""
from __future__ import annotations

import hashlib
import json
import sys
from typing import Any


def hash_inputs(data: Any) -> str:
    """Return SHA-256 hex digest of the canonical JSON representation of *data*."""
    canonical = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def summarize_output(data: Any) -> dict[str, Any]:
    """Return a structural summary safe to store in telemetry spans.

    Never includes actual field values — only shape, size, and error presence.
    """
    summary: dict[str, Any] = {
        "has_error": False,
        "size_bytes": 0,
        "field_count": 0,
        "schema_version": None,
    }

    try:
        serialized = json.dumps(data, default=str)
        summary["size_bytes"] = sys.getsizeof(serialized)
    except Exception:
        summary["has_error"] = True
        return summary

    if isinstance(data, dict):
        summary["field_count"] = len(data)
        summary["schema_version"] = data.get("schema_version") or data.get("version")
        summary["has_error"] = "error" in data or "errors" in data
    elif isinstance(data, list):
        summary["field_count"] = len(data)
    elif data is None:
        summary["has_error"] = True

    return summary
