"""TelemetryCircuitBreaker — pybreaker wrapper that emits OTEL span events and a gauge metric.

State transitions emit both a span event (for trace correlation) and update a Gauge
(for alerting). Gauge values: 0=CLOSED, 1=HALF_OPEN, 2=OPEN.
"""
from __future__ import annotations

from typing import Any

import pybreaker
from opentelemetry import trace, metrics

# Keyed by lowercase state name to handle both pybreaker < 1.0 ("half-open")
# and pybreaker >= 1.0 ("half_open") without relying on module-level constants
# that may not exist or whose values may differ across versions.
_STATE_MAP = {
    "closed": 0,
    "half_open": 1,
    "half-open": 1,
    "open": 2,
}


class _TelemetryListener(pybreaker.CircuitBreakerListener):
    def __init__(self, name: str, gauge: Any) -> None:
        self._name = name
        self._gauge = gauge

    def state_change(self, cb: pybreaker.CircuitBreaker, old_state: Any, new_state: Any) -> None:
        self._gauge.set(
            _STATE_MAP.get(new_state.name.lower(), -1),
            {"circuit_breaker.name": self._name},
        )
        span = trace.get_current_span()
        if span.is_recording():
            span.add_event(
                "circuit_breaker.state_change",
                {
                    "circuit_breaker.name": self._name,
                    "circuit_breaker.old_state": old_state.name,
                    "circuit_breaker.new_state": new_state.name,
                },
            )


class TelemetryCircuitBreaker:
    """A circuit breaker that emits OTEL signals on every state transition.

    Usage:
        breaker = TelemetryCircuitBreaker(name="azure_openai_primary", fail_max=5, reset_timeout=30)

        @breaker
        async def call_llm(prompt): ...
    """

    def __init__(
        self,
        name: str,
        fail_max: int = 5,
        reset_timeout: int = 30,
    ) -> None:
        meter = metrics.get_meter(__name__)
        gauge = meter.create_gauge(
            "circuit_breaker.state",
            description="Circuit breaker state: 0=CLOSED, 1=HALF_OPEN, 2=OPEN",
        )
        gauge.set(0, {"circuit_breaker.name": name})

        listener = _TelemetryListener(name, gauge)
        self._breaker = pybreaker.CircuitBreaker(
            fail_max=fail_max,
            reset_timeout=reset_timeout,
            listeners=[listener],
            name=name,
        )

    def __call__(self, fn: Any) -> Any:
        return self._breaker(fn)

    @property
    def current_state(self) -> str:
        return self._breaker.current_state
