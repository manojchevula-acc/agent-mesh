"""Metric instruments, created lazily on the global meter (names from ``gernas.Metric``)."""

from __future__ import annotations

import threading
from typing import Any

from opentelemetry import metrics

from gernas._version import __version__

_lock = threading.Lock()
_counters: dict[str, Any] = {}
_histograms: dict[str, Any] = {}


def _meter() -> metrics.Meter:
    return metrics.get_meter("gernas", __version__)


def _counter(name: str) -> Any:
    inst = _counters.get(name)
    if inst is None:
        with _lock:
            inst = _counters.get(name) or _meter().create_counter(name)
            _counters[name] = inst
    return inst


def _histogram(name: str) -> Any:
    inst = _histograms.get(name)
    if inst is None:
        with _lock:
            inst = _histograms.get(name) or _meter().create_histogram(name, unit="ms")
            _histograms[name] = inst
    return inst


def record(
    counter: str, attrs: dict, *, histogram: str | None = None, duration_ms: float | None = None, n: float = 1
) -> None:
    """Best-effort metric write used by the decorators; never raises."""
    try:
        clean = {k: v for k, v in attrs.items() if v is not None}
        _counter(counter).add(n, clean)
        if histogram and duration_ms is not None:
            _histogram(histogram).record(duration_ms, clean)
    except Exception:  # noqa: BLE001
        pass
