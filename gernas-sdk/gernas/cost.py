"""Rate-card cost estimation (Blueprint §2 cost context ``estimated_cost``, §11 cost model).

The rate card is a JSON file named by ``GERNAS_RATE_CARD_PATH``; costs are in its
``currency`` (AED for FAB)::

    {"version": "2026-04-01", "currency": "AED",
     "rates": [{"model": "gpt-4o", "provider": "azure_openai", "input_per_1k": 0.0092, "output_per_1k": 0.0368}]}

``provider`` may be ``"*"``. Without a card, or without a matching entry, no cost is
recorded — never an invented value and never an error.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}


def _load(path: str) -> dict | None:
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    with _lock:
        hit = _cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    with _lock:
        _cache[path] = (mtime, data)
    return data


def estimate_cost(
    model_id: str | None,
    provider: str | None,
    token_input: int | None,
    token_output: int | None,
    rate_card_path: str | None,
) -> float | None:
    """Estimated cost of one call, or None when it cannot be priced."""
    if not model_id or not rate_card_path or (token_input is None and token_output is None):
        return None
    card = _load(rate_card_path)
    if not card:
        return None
    for rate in card.get("rates", []):
        if str(rate.get("model", "")).lower() != model_id.lower():
            continue
        if str(rate.get("provider", "*")).lower() not in ("*", (provider or "").lower()):
            continue
        try:
            cost = (token_input or 0) / 1000.0 * float(rate["input_per_1k"]) + (token_output or 0) / 1000.0 * float(
                rate.get("output_per_1k", 0.0)
            )
        except (KeyError, TypeError, ValueError):
            return None
        return round(cost, 8)
    return None
