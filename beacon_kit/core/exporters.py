"""ExporterFactory — build span and metric exporters from TelemetryConfig.

Switching backends requires only an env var change; no application code changes.

Exporter types:
  console          — stdout (OTEL SDK default pretty-print)
  pretty           — rich-formatted stdout (beacon_kit PrettyExporter)
  otel_collector   — OTLP/gRPC to a local collector (e.g. Jaeger, Aspire)
  grafana          — OTLP/HTTP to Grafana Cloud with Basic auth
  grafana_and_file — OTLP/HTTP to Grafana Cloud AND local JSONL files (primary exporter only;
                     provider.py adds the secondary file processor)
  file             — local JSONL files only (no network export)
  azure_monitor    — Azure Application Insights
  off              — no-op (discard all signals)
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Tuple

from opentelemetry.sdk.trace.export import SpanExporter, ConsoleSpanExporter
from opentelemetry.sdk.metrics.export import MetricExporter, ConsoleMetricExporter

if TYPE_CHECKING:
    from beacon_kit.core.config import TelemetryConfig


def build_span_exporter(config: "TelemetryConfig") -> SpanExporter:
    if config.exporter_type == "off":
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: F401
        # Return a no-op exporter that discards all spans
        class _NoOpSpanExporter(SpanExporter):
            def export(self, spans): return SpanExportResult.SUCCESS  # type: ignore[return-value]
            def shutdown(self): pass
        return _NoOpSpanExporter()

    if config.exporter_type == "azure_monitor":
        try:
            from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
            return AzureMonitorTraceExporter(connection_string=config.azure_connection_string)
        except ImportError as exc:
            raise ImportError(
                "Install azure extras: pip install beacon_kit[azure]"
            ) from exc

    if config.exporter_type == "otel_collector":
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        return OTLPSpanExporter(endpoint=config.otel_endpoint)

    if config.exporter_type == "pretty":
        from beacon_kit.core.pretty_exporter import PrettySpanExporter
        return PrettySpanExporter()

    if config.exporter_type in ("grafana", "grafana_and_file"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter as OTLPSpanExporterHTTP
        import base64
        headers = _grafana_auth_headers(config)
        return OTLPSpanExporterHTTP(endpoint=config.otel_endpoint, headers=headers)

    if config.exporter_type == "file":
        from beacon_kit.core.file_exporters import FileSpanExporter
        return FileSpanExporter(config.file_path("traces"))

    # Default: console (dev)
    return ConsoleSpanExporter()


def build_metric_exporter(config: "TelemetryConfig") -> MetricExporter:
    if config.exporter_type == "off":
        class _NoOpMetricExporter(MetricExporter):
            def export(self, metrics_data, **kw): return MetricExportResult.SUCCESS  # type: ignore[return-value]
            def force_flush(self, timeout_millis=10000): return True
            def shutdown(self, timeout_millis=30000, **kw): pass
        return _NoOpMetricExporter()

    if config.exporter_type == "azure_monitor":
        try:
            from azure.monitor.opentelemetry.exporter import AzureMonitorMetricsExporter
            return AzureMonitorMetricsExporter(connection_string=config.azure_connection_string)
        except ImportError as exc:
            raise ImportError(
                "Install azure extras: pip install beacon_kit[azure]"
            ) from exc

    if config.exporter_type == "otel_collector":
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        return OTLPMetricExporter(endpoint=config.otel_endpoint)

    if config.exporter_type == "pretty":
        from beacon_kit.core.pretty_exporter import PrettyMetricExporter
        return PrettyMetricExporter()

    if config.exporter_type in ("grafana", "grafana_and_file"):
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter as OTLPMetricExporterHTTP
        headers = _grafana_auth_headers(config)
        return OTLPMetricExporterHTTP(endpoint=config.otel_endpoint, headers=headers)

    if config.exporter_type == "file":
        from beacon_kit.core.file_exporters import FileMetricExporter
        return FileMetricExporter(config.file_path("metrics"))

    return ConsoleMetricExporter()


def build_log_exporter(config: "TelemetryConfig"):
    """Build an OTEL log record exporter matching the configured backend."""
    if config.exporter_type == "off":
        try:
            from opentelemetry.sdk._logs.export import LogExporter, LogExportResult
            class _NoOpLogExporter(LogExporter):
                def export(self, batch): return LogExportResult.SUCCESS  # type: ignore[return-value]
                def shutdown(self): pass
            return _NoOpLogExporter()
        except ImportError:
            pass

    if config.exporter_type == "pretty":
        from beacon_kit.core.pretty_exporter import PrettyLogExporter
        return PrettyLogExporter()

    if config.exporter_type == "otel_collector":
        try:
            from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
            return OTLPLogExporter(endpoint=config.otel_endpoint)
        except ImportError as exc:
            raise ImportError("Install otlp extras: pip install opentelemetry-exporter-otlp-proto-grpc") from exc

    if config.exporter_type == "azure_monitor":
        try:
            from azure.monitor.opentelemetry.exporter import AzureMonitorLogExporter
            return AzureMonitorLogExporter(connection_string=config.azure_connection_string)
        except ImportError as exc:
            raise ImportError("Install azure extras: pip install beacon_kit[azure]") from exc

    if config.exporter_type in ("grafana", "grafana_and_file"):
        try:
            from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter as OTLPLogExporterHTTP
            headers = _grafana_auth_headers(config)
            return OTLPLogExporterHTTP(endpoint=config.otel_endpoint, headers=headers)
        except ImportError as exc:
            raise ImportError("Install otlp-http extras: pip install opentelemetry-exporter-otlp-proto-http") from exc

    if config.exporter_type == "file":
        from beacon_kit.core.file_exporters import FileLogExporter
        return FileLogExporter(config.file_path("logs"))

    from opentelemetry.sdk._logs.export import ConsoleLogExporter
    return ConsoleLogExporter()


def build_file_exporters(config: "TelemetryConfig") -> "Tuple":
    """Return (FileSpanExporter, FileMetricExporter, FileLogExporter) for dual-export wiring.

    Called by provider.py when file_output_dir is set. Callers add these as secondary
    processors alongside the primary (Grafana/console) processors.
    """
    from beacon_kit.core.file_exporters import FileSpanExporter, FileMetricExporter, FileLogExporter
    return (
        FileSpanExporter(config.file_path("traces")),
        FileMetricExporter(config.file_path("metrics")),
        FileLogExporter(config.file_path("logs")),
    )


def _grafana_auth_headers(config: "TelemetryConfig") -> dict:
    """Build Basic-auth headers for Grafana Cloud OTLP/HTTP endpoints."""
    import base64
    token = getattr(config, "grafana_api_key", "") or ""
    instance_id = getattr(config, "grafana_instance_id", "") or ""
    if instance_id and token:
        creds = base64.b64encode(f"{instance_id}:{token}".encode()).decode()
        return {"Authorization": f"Basic {creds}"}
    return {}
