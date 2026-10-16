"""httpx event hooks that inject GERNAS trace context into outgoing requests.

Built from OTel propagation primitives (no ``opentelemetry-instrumentation-httpx``)::

    httpx.AsyncClient(event_hooks=async_event_hooks())
    httpx.Client(event_hooks=sync_event_hooks())
"""
from __future__ import annotations

from typing import Any

from gernas.async_context import inject_context


def _inject(request: Any) -> None:
    try:
        inject_context(request.headers)
    except Exception:  # noqa: BLE001
        pass


async def _ainject(request: Any) -> None:
    _inject(request)


def sync_event_hooks() -> dict[str, list]:
    return {"request": [_inject]}


def async_event_hooks() -> dict[str, list]:
    return {"request": [_ainject]}
