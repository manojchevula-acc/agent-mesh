"""Single activation point for mesh observability.

Call :func:`setup_observability` once per process at startup (before any agents
or chat clients are built) so the Microsoft Agent Framework SDK picks up the
configured OpenTelemetry providers and emits its native spans/metrics:

  - ``invoke_agent <name>``   (agent invocation, tokens, model)
  - ``chat <model>``          (LLM call)
  - ``execute_tool <fn>``     (function/MCP tool call)
  - ``workflow.run`` / ``executor.process`` / ``edge_group.process`` (workflow)

Plus GenAI metrics: ``gen_ai.client.operation.duration``,
``gen_ai.client.token.usage``, ``agent_framework.function.invocation.duration``.

Exporter wiring is selected by ``Config.OBS_PROFILE``:
  - ``dev``     -> ``configure_otel_providers()`` (console + OTLP, e.g. Aspire/Jaeger)
  - ``grafana`` -> Grafana Cloud OTLP/HTTP (Tempo + Mimir + Loki) with Basic auth
  - ``prod``    -> ``configure_azure_monitor(...)`` + ``enable_instrumentation()``
  - ``off``     -> file logging only (no OTel providers)

Framework-first: we rely on the SDK's built-in instrumentation rather than a
parallel custom tracing model. Custom spans are added only where the framework
has no equivalent (the orchestrator root span + deterministic gate executors).
"""
from __future__ import annotations

import logging
import os

from src.config import Config
from src.observability.logging_config import CAT_SYSTEM, configure_logging

_INITIALIZED = False

# Module-level provider references populated by _setup_grafana().
# Used by flush_observability() to force-flush pending telemetry on shutdown.
_tracer_provider = None
_meter_provider = None
_logger_provider = None


def _set_otel_env() -> None:
    """Mirror Config values into the standard OTel env vars the SDK reads."""
    os.environ.setdefault("OTEL_SERVICE_NAME", Config.OTEL_SERVICE_NAME)
    if Config.OTEL_EXPORTER_OTLP_ENDPOINT:
        os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", Config.OTEL_EXPORTER_OTLP_ENDPOINT)
    if Config.ENABLE_INSTRUMENTATION:
        os.environ.setdefault("ENABLE_INSTRUMENTATION", "true")
    if Config.ENABLE_SENSITIVE_DATA:
        os.environ.setdefault("ENABLE_SENSITIVE_DATA", "true")
    if Config.ENABLE_CONSOLE_EXPORTERS:
        os.environ.setdefault("ENABLE_CONSOLE_EXPORTERS", "true")


def flush_observability(timeout_ms: int = 5000) -> None:
    """Force-flush pending spans, metrics, and logs to the configured exporter.

    Call this before process shutdown to avoid losing telemetry that hasn't
    been exported yet. The Grafana metrics reader exports every
    ``GRAFANA_EXPORT_INTERVAL_MS`` (default 15 s); without an explicit flush,
    any metrics recorded in the final window are silently dropped on exit.
    """
    for provider in (_tracer_provider, _meter_provider, _logger_provider):
        if provider is not None:
            try:
                provider.force_flush(timeout_millis=timeout_ms)
            except Exception:
                pass


def setup_observability(service_name: str | None = None) -> None:
    """Activate logging + framework-native OpenTelemetry for this process.

    Idempotent and crash-safe. ``service_name`` overrides ``OTEL_SERVICE_NAME``
    so each mesh node (process) gets its own service identity in the trace tree.
    """
    global _INITIALIZED
    if _INITIALIZED:
        return

    if service_name:
        os.environ["OTEL_SERVICE_NAME"] = service_name
        # Config is read at import; keep the attribute in sync for our own logs.
        Config.OTEL_SERVICE_NAME = service_name

    # 1) Logging FIRST so import-time and setup logs are captured.
    configure_logging(service_name)
    log = logging.getLogger(CAT_SYSTEM)

    profile = (Config.OBS_PROFILE or "dev").lower()
    if profile == "off":
        log.info("Observability profile=off: file logging only, OTel disabled.")
        _INITIALIZED = True
        return

    _set_otel_env()

    try:
        if profile == "prod":
            _setup_prod(log)
        elif profile == "grafana":
            _setup_grafana(log)
        else:
            _setup_dev(log)
    except Exception as exc:  # never crash the app on telemetry setup
        log.warning("Observability activation failed (%s); continuing with logging only.", exc)

    # Propagate W3C trace context across A2A hops by instrumenting httpx. This
    # injects traceparent/tracestate onto A2AAgent's OWN client (we must not
    # replace that client — doing so breaks A2A response parsing). No-op if the
    # instrumentation package isn't installed.
    _instrument_httpx(log)

    _INITIALIZED = True


def _instrument_httpx(log: logging.Logger) -> None:
    """Enable OpenTelemetry httpx instrumentation for A2A trace propagation."""
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
        log.info("httpx instrumentation enabled (A2A trace propagation active).")
    except Exception as exc:
        log.info(
            "httpx instrumentation unavailable (%s); A2A spans will not be "
            "cross-process linked. Install opentelemetry-instrumentation-httpx.",
            exc,
        )


def _setup_dev(log: logging.Logger) -> None:
    """Dev: framework OTel providers exporting to console and/or OTLP."""
    from agent_framework.observability import configure_otel_providers

    configure_otel_providers(enable_sensitive_data=Config.ENABLE_SENSITIVE_DATA)
    log.info(
        "Observability profile=dev: OTLP endpoint=%s, console=%s, sensitive=%s",
        Config.OTEL_EXPORTER_OTLP_ENDPOINT, Config.ENABLE_CONSOLE_EXPORTERS,
        Config.ENABLE_SENSITIVE_DATA,
    )


def _setup_grafana(log: logging.Logger) -> None:
    """Grafana Cloud: OTLP/HTTP → Tempo (traces), Mimir (metrics), Loki (logs).

    Auth: Basic base64(GRAFANA_INSTANCE_ID:GRAFANA_API_TOKEN)
    Falls back to _setup_dev() if any credential is missing.
    """
    global _tracer_provider, _meter_provider, _logger_provider

    import base64

    endpoint = Config.GRAFANA_OTLP_ENDPOINT
    instance_id = Config.GRAFANA_INSTANCE_ID
    api_token = Config.GRAFANA_API_TOKEN

    if not endpoint or not instance_id or not api_token:
        log.warning(
            "OBS_PROFILE=grafana but credentials incomplete "
            "(GRAFANA_OTLP_ENDPOINT=%r, GRAFANA_INSTANCE_ID=%r, GRAFANA_API_TOKEN=%s); "
            "falling back to OTLP/console providers.",
            endpoint or "<unset>",
            instance_id or "<unset>",
            "***" if api_token else "<unset>",
        )
        _setup_dev(log)
        return

    raw = f"{instance_id}:{api_token}".encode("utf-8")
    auth_value = "Basic " + base64.b64encode(raw).decode("ascii")
    headers = {"Authorization": auth_value}
    base = endpoint.rstrip("/")

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.sdk.metrics import MeterProvider, Histogram as _Histogram
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader, AggregationTemporality
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry import trace as _trace, metrics as _metrics
    from opentelemetry._logs import set_logger_provider
    from agent_framework.observability import create_resource, enable_instrumentation

    resource = create_resource()

    # Traces → Grafana Tempo
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(
            endpoint=base + "/v1/traces",
            headers=headers,
            timeout=30,
        ))
    )
    _trace.set_tracer_provider(tracer_provider)
    _tracer_provider = tracer_provider

    # Metrics → Grafana Mimir
    # export_timeout_millis must be < export_interval_millis to avoid deadline
    # exhaustion that produces a negative connect-timeout in urllib3.
    # Force CUMULATIVE temporality for histograms: OTel SDK ≥ 1.20 defaults
    # histograms to DELTA, which breaks Prometheus rate() queries in Mimir.
    # GRAFANA_EXPORT_INTERVAL_MS defaults to 15 s (was 60 s) so metrics appear
    # in Grafana quickly during development and are not lost on short sessions.
    export_interval = Config.GRAFANA_EXPORT_INTERVAL_MS
    export_timeout = min(export_interval - 5_000, 25_000)
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[PeriodicExportingMetricReader(
            OTLPMetricExporter(
                endpoint=base + "/v1/metrics",
                headers=headers,
                timeout=30,
                preferred_temporality={_Histogram: AggregationTemporality.CUMULATIVE},
            ),
            export_interval_millis=export_interval,
            export_timeout_millis=export_timeout,
        )],
    )
    _metrics.set_meter_provider(meter_provider)
    _meter_provider = meter_provider

    # Logs → Grafana Loki (attaches to root logger so all mesh loggers flow through)
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(
            endpoint=base + "/v1/logs",
            headers=headers,
            timeout=30,
        ))
    )
    set_logger_provider(logger_provider)
    logging.getLogger().addHandler(
        LoggingHandler(level=logging.DEBUG, logger_provider=logger_provider)
    )
    _logger_provider = logger_provider

    enable_instrumentation(enable_sensitive_data=Config.ENABLE_SENSITIVE_DATA)
    log.info(
        "Observability profile=grafana: OTLP/HTTP → %s (Tempo + Mimir + Loki), "
        "metrics interval=%ds.",
        endpoint,
        export_interval // 1000,
    )


def _setup_prod(log: logging.Logger) -> None:
    """Prod: Azure Monitor / Application Insights, then activate AF instrumentation."""
    conn = Config.APPLICATIONINSIGHTS_CONNECTION_STRING
    if not conn:
        log.warning(
            "OBS_PROFILE=prod but APPLICATIONINSIGHTS_CONNECTION_STRING is unset; "
            "falling back to OTLP/console providers."
        )
        _setup_dev(log)
        return

    from azure.monitor.opentelemetry import configure_azure_monitor
    from agent_framework.observability import create_resource, enable_instrumentation

    configure_azure_monitor(
        connection_string=conn,
        resource=create_resource(),
        enable_live_metrics=True,
    )
    # Activate Agent Framework's telemetry code paths on the Azure-configured providers.
    enable_instrumentation(enable_sensitive_data=Config.ENABLE_SENSITIVE_DATA)
    log.info("Observability profile=prod: Azure Monitor active (live metrics on).")
