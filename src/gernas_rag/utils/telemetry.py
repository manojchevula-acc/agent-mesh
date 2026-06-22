"""OpenTelemetry setup.

Telemetry is best-effort: if the OTLP exporter or SDK is unavailable, telemetry
is silently disabled so it never blocks the service from starting.
"""

from .logging import get_logger

logger = get_logger(__name__)

_configured = False


def configure_telemetry(service_name: str) -> None:
    """Configure a global tracer provider for ``service_name``.

    Safe to call multiple times; subsequent calls are no-ops.
    """
    global _configured
    if _configured:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider

        resource = Resource.create({SERVICE_NAME: service_name})
        provider = TracerProvider(resource=resource)
        trace.set_tracer_provider(provider)
        _configured = True
        logger.info("Telemetry configured", service_name=service_name)
    except Exception as exc:  # pragma: no cover - optional dependency path
        logger.warning("Telemetry disabled", error=str(exc))


def instrument_fastapi(app: object) -> None:
    """Instrument a FastAPI app if the instrumentation package is present."""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)  # type: ignore[arg-type]
        logger.info("FastAPI instrumentation enabled")
    except Exception as exc:  # pragma: no cover - optional dependency path
        logger.warning("FastAPI instrumentation disabled", error=str(exc))
