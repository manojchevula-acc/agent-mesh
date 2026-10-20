"""Exporter abstraction (Blueprint §1): OTLP to the Collector, or console / off.

The SDK never talks to a backend directly; the Collector routes to Opik, the data
plane or any future vendor, so FAB can change backends without re-instrumenting.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, MetricExporter, MetricExportResult
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SpanExporter, SpanExportResult

from gernas.config import GernasConfig


class NoOpSpanExporter(SpanExporter):
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass


class NoOpMetricExporter(MetricExporter):
    def export(self, metrics_data: Any, timeout_millis: float = 10_000, **kwargs: Any) -> MetricExportResult:
        return MetricExportResult.SUCCESS

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        return True

    def shutdown(self, timeout_millis: float = 30_000, **kwargs: Any) -> None:
        pass


class _NoOpLogExporter:
    def export(self, batch: Sequence[Any]) -> Any:
        from opentelemetry.sdk._logs.export import LogExportResult

        return LogExportResult.SUCCESS

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True

    def shutdown(self) -> None:
        pass


def _headers(cfg: GernasConfig) -> dict[str, str] | None:
    return {"authorization": f"Bearer {cfg.otlp_api_key}"} if cfg.otlp_api_key else None


def _insecure(cfg: GernasConfig) -> bool:
    return cfg.otlp_endpoint.startswith("http://") or "://" not in cfg.otlp_endpoint


def _http_url(cfg: GernasConfig, signal: str) -> str:
    base = cfg.otlp_endpoint.rstrip("/")
    if base.endswith(f"/v1/{signal}"):
        return base
    if base.endswith(":4317"):
        base = base[: -len("4317")] + "4318"
    return f"{base}/v1/{signal}"


def build_span_exporter(cfg: GernasConfig) -> SpanExporter:
    if cfg.exporter == "off":
        return NoOpSpanExporter()
    if cfg.exporter == "console":
        return ConsoleSpanExporter()
    if cfg.otlp_transport == "http":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        return OTLPSpanExporter(endpoint=_http_url(cfg, "traces"), headers=_headers(cfg), timeout=cfg.otlp_timeout_s)
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter as GrpcSpanExporter

    return GrpcSpanExporter(
        endpoint=cfg.otlp_endpoint, insecure=_insecure(cfg), headers=_headers(cfg), timeout=cfg.otlp_timeout_s
    )


def build_metric_exporter(cfg: GernasConfig) -> MetricExporter:
    if cfg.exporter == "off":
        return NoOpMetricExporter()
    if cfg.exporter == "console":
        return ConsoleMetricExporter()
    if cfg.otlp_transport == "http":
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

        return OTLPMetricExporter(endpoint=_http_url(cfg, "metrics"), headers=_headers(cfg), timeout=cfg.otlp_timeout_s)
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter as GrpcMetricExporter

    return GrpcMetricExporter(
        endpoint=cfg.otlp_endpoint, insecure=_insecure(cfg), headers=_headers(cfg), timeout=cfg.otlp_timeout_s
    )


def build_log_exporter(cfg: GernasConfig) -> Any:
    if cfg.exporter == "off":
        return _NoOpLogExporter()
    if cfg.exporter == "console":
        try:
            from opentelemetry.sdk._logs.export import ConsoleLogRecordExporter
        except ImportError:  # opentelemetry-sdk < 1.39
            from opentelemetry.sdk._logs.export import ConsoleLogExporter as ConsoleLogRecordExporter

        return ConsoleLogRecordExporter()
    if cfg.otlp_transport == "http":
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

        return OTLPLogExporter(endpoint=_http_url(cfg, "logs"), headers=_headers(cfg), timeout=cfg.otlp_timeout_s)
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter as GrpcLogExporter

    return GrpcLogExporter(
        endpoint=cfg.otlp_endpoint, insecure=_insecure(cfg), headers=_headers(cfg), timeout=cfg.otlp_timeout_s
    )
