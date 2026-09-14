"""Telemetry stub — tracing is handled by Opik (configured in main.py lifespan).

Kept so any remaining imports don't break.
"""

from .logging import get_logger

logger = get_logger(__name__)


def configure_telemetry(service_name: str) -> None:
    """No-op — Opik is configured directly in main.py lifespan."""
    pass


def instrument_fastapi(app: object) -> None:
    """No-op — Opik traces are emitted via @opik.track decorators."""
    pass
