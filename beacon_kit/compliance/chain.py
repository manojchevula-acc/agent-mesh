"""Compliance event chain hashing and HMAC signing.

Each compliance event embeds a SHA-256 hash of the previous event, creating
a tamper-detectable append-only chain. HMAC-SHA256 signs each event.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any


def compute_chain_hash(event: dict[str, Any], previous_hash: str) -> str:
    """Return SHA-256 of ``previous_hash + canonical_json(event)``."""
    canonical = previous_hash + json.dumps(event, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def sign_event(event: dict[str, Any], key: str) -> str:
    """Return HMAC-SHA256 hex digest of canonical JSON, using *key*.

    In production *key* should be fetched from Azure Key Vault.
    """
    canonical = json.dumps(event, sort_keys=True, default=str).encode()
    return hmac.new(key.encode(), canonical, digestmod=hashlib.sha256).hexdigest()


def verify_chain(events: list[dict[str, Any]]) -> bool:
    """Verify that the chain_hash field on each event is consistent with the sequence."""
    previous_hash = "0" * 64
    for event in events:
        expected = compute_chain_hash(
            {k: v for k, v in event.items() if k not in ("chain_hash", "signature")},
            previous_hash,
        )
        if event.get("chain_hash") != expected:
            return False
        previous_hash = event["chain_hash"]
    return True
