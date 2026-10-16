"""Rate-card cost enrichment for model calls (WP1-CORE-02a step 4).

The rate card is a JSON file named by ``GERNAS_RATE_CARD_PATH``::

    {"version": "2026-04-01", "currency": "AED",
     "rates": [{"model": "openai/gpt-oss-120b", "provider": "groq",
                "input_per_1k": 0.0092, "output_per_1k": 0.0368}]}

``provider`` may be ``"*"``. If the card is absent, unreadable, or has no entry
for ``(model, provider)`` the cost is simply not recorded — never an error.
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


def estimate_cost_aed(model_id: str | None, provider: str | None, input_tokens: int | None,
                      output_tokens: int | None, rate_card_path: str | None = None) -> tuple[float, str] | None:
    """Return ``(cost_aed, rate_card_version)`` or ``None`` when it cannot be priced."""
    if not model_id or (input_tokens is None and output_tokens is None):
        return None
    path = rate_card_path if rate_card_path is not None else os.getenv("GERNAS_RATE_CARD_PATH", "")
    if not path:
        return None
    card = _load(path)
    if not card:
        return None
    model = model_id.lower()
    prov = (provider or "").lower()
    for rate in card.get("rates", []):
        if str(rate.get("model", "")).lower() != model:
            continue
        rprov = str(rate.get("provider", "*")).lower()
        if rprov not in ("*", prov):
            continue
        try:
            cost = ((input_tokens or 0) / 1000.0) * float(rate["input_per_1k"]) + \
                   ((output_tokens or 0) / 1000.0) * float(rate["output_per_1k"])
        except (KeyError, TypeError, ValueError):
            return None
        return round(cost, 8), str(card.get("version", "unversioned"))
    return None
