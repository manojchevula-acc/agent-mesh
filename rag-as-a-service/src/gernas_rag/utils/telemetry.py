"""OpenTelemetry setup.

Telemetry is best-effort: if the OTLP exporter or SDK is unavailable, telemetry
is silently disabled so it never blocks the service from starting.

The provider built here is what collects MAF's native ``workflow.run`` and
``executor.process`` spans — six per retrieval request, one per executor. A
provider with no span processor records those spans and drops them, which is what
this module used to do, so attaching an exporter is the difference between the
workflow migration being observable and merely being instrumented.
"""

import os

from .logging import get_logger

logger = get_logger(__name__)

_configured = False

# Standard OTel variable, so an operator configures this the same way they would
# any other OTel service. Unset means "export nothing" — spans are still recorded
# in-process, exactly as before, so this stays opt-in.
_ENDPOINT_VAR = "OTEL_EXPORTER_OTLP_ENDPOINT"
# "grpc" (default, the exporter installed here) or "http/protobuf". The HTTP
# exporter ships in a separate distribution
# (opentelemetry-exporter-otlp-proto-http) which is NOT currently installed, so
# asking for it without adding that dependency lands in the warning path rather
# than crashing startup.
_PROTOCOL_VAR = "OTEL_EXPORTER_OTLP_PROTOCOL"


def _build_span_exporter(endpoint: str, protocol: str):
    """Return an OTLP span exporter, or None if its distribution is missing.

    Import is deferred and per-protocol because the gRPC and HTTP exporters are
    separate packages; picking one at import time would make the other's absence
    a startup failure.
    """
    try:
        if protocol.startswith("http"):
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
        else:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )
    except ImportError as exc:
        logger.warning(
            "Span export disabled — OTLP exporter package not installed",
            protocol=protocol,
            error=str(exc),
        )
        return None
    return OTLPSpanExporter(endpoint=endpoint)


def configure_telemetry(service_name: str) -> None:
    """Configure a global tracer provider for ``service_name``.

    Safe to call multiple times; subsequent calls are no-ops.

    A span processor is attached only when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set.
    Without it the provider behaves as it always has — spans are recorded and
    discarded — so enabling collection is a configuration change, not a code one.
    """
    global _configured
    if _configured:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create({SERVICE_NAME: service_name})
        provider = TracerProvider(resource=resource)

        endpoint = os.getenv(_ENDPOINT_VAR)
        if endpoint:
            protocol = os.getenv(_PROTOCOL_VAR, "grpc")
            exporter = _build_span_exporter(endpoint, protocol)
            if exporter is not None:
                # Batched rather than simple: a synchronous export on the request
                # path would add the collector's latency to every retrieval.
                provider.add_span_processor(BatchSpanProcessor(exporter))
                logger.info("Span export enabled", endpoint=endpoint, protocol=protocol)
        else:
            logger.warning(
                "Span export disabled — spans are recorded and dropped",
                hint=f"set {_ENDPOINT_VAR} to collect workflow.run / executor.process spans",
            )

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
