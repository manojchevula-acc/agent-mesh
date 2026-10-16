"""Exporter factory — the destination is always the OTel Collector.

``GERNAS_EXPORTER=otlp`` (default) sends traces, metrics and logs to
``GERNAS_OTLP_ENDPOINT`` over gRPC, or OTLP/HTTP with ``GERNAS_OTLP_TRANSPORT=http``.
The Collector fans out to Opik and the data plane; the SDK never talks to a
backend directly (principle 5). ``console``/``pretty``/``file``/``memory``/``off``
are local developer sinks.
"""
from __future__ import annotations

from typing import Sequence

from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    InMemoryMetricReader,  # noqa: F401 - re-exported for tests
    MetricExporter,
    MetricExportResult,
)
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from gernas.config import GernasConfig


class NoOpSpanExporter(SpanExporter):
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass


class NoOpMetricExporter(MetricExporter):
    def __init__(self) -> None:
        super().__init__()

    def export(self, metrics_data, timeout_millis: float = 10_000, **kwargs) -> MetricExportResult:
        return MetricExportResult.SUCCESS

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        return True

    def shutdown(self, timeout_millis: float = 30_000, **kwargs) -> None:
        pass


_memory_exporter: InMemorySpanExporter | None = None


def memory_span_exporter() -> InMemorySpanExporter:
    """Process-wide in-memory exporter used by ``GERNAS_EXPORTER=memory``."""
    global _memory_exporter
    if _memory_exporter is None:
        _memory_exporter = InMemorySpanExporter()
    return _memory_exporter


def _headers(cfg: GernasConfig) -> dict[str, str] | None:
    return {"authorization": f"Bearer {cfg.otlp_api_key}"} if cfg.otlp_api_key else None


def _grpc_target(cfg: GernasConfig) -> tuple[str, bool]:
    endpoint = cfg.otlp_endpoint
    insecure = endpoint.startswith("http://") or "://" not in endpoint
    return endpoint, insecure


def _http_url(cfg: GernasConfig, signal: str) -> str:
    base = cfg.otlp_endpoint.rstrip("/")
    if base.endswith(f"/v1/{signal}"):
        return base
    if base.endswith(":4317"):
        base = base[: -len("4317")] + "4318"
    return f"{base}/v1/{signal}"


def build_span_exporter(cfg: GernasConfig) -> SpanExporter:
    kind = cfg.exporter
    if kind == "off":
        return NoOpSpanExporter()
    if kind == "memory":
        return memory_span_exporter()
    if kind == "console":
        return ConsoleSpanExporter()
    if kind == "pretty":
        from gernas.core.pretty_exporter import PrettySpanExporter
        return PrettySpanExporter()
    if kind == "file":
        from gernas.core.file_exporters import FileSpanExporter
        return FileSpanExporter(cfg.file_path("traces"))
    if cfg.otlp_transport == "http":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        return OTLPSpanExporter(endpoint=_http_url(cfg, "traces"), headers=_headers(cfg), timeout=cfg.otlp_timeout_s)
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter as GrpcSpanExporter
    endpoint, insecure = _grpc_target(cfg)
    return GrpcSpanExporter(endpoint=endpoint, insecure=insecure, headers=_headers(cfg), timeout=cfg.otlp_timeout_s)


def build_metric_exporter(cfg: GernasConfig) -> MetricExporter:
    kind = cfg.exporter
    if kind in ("off", "memory"):
        return NoOpMetricExporter()
    if kind == "console":
        return ConsoleMetricExporter()
    if kind == "pretty":
        from gernas.core.pretty_exporter import PrettyMetricExporter
        return PrettyMetricExporter()
    if kind == "file":
        from gernas.core.file_exporters import FileMetricExporter
        return FileMetricExporter(cfg.file_path("metrics"))
    if cfg.otlp_transport == "http":
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        return OTLPMetricExporter(endpoint=_http_url(cfg, "metrics"), headers=_headers(cfg), timeout=cfg.otlp_timeout_s)
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter as GrpcMetricExporter
    endpoint, insecure = _grpc_target(cfg)
    return GrpcMetricExporter(endpoint=endpoint, insecure=insecure, headers=_headers(cfg), timeout=cfg.otlp_timeout_s)


def build_log_exporter(cfg: GernasConfig):
    kind = cfg.exporter
    from opentelemetry.sdk._logs.export import ConsoleLogExporter

    if kind in ("off", "memory"):
        try:
            from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter as _Mem
        except ImportError:  # OTel < 1.39
            from opentelemetry.sdk._logs.export import InMemoryLogExporter as _Mem  # type: ignore[no-redef]
        return _Mem()
    if kind == "console":
        return ConsoleLogExporter()
    if kind == "pretty":
        from gernas.core.pretty_exporter import PrettyLogExporter
        return PrettyLogExporter()
    if kind == "file":
        from gernas.core.file_exporters import FileLogExporter
        return FileLogExporter(cfg.file_path("logs"))
    if cfg.otlp_transport == "http":
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        return OTLPLogExporter(endpoint=_http_url(cfg, "logs"), headers=_headers(cfg), timeout=cfg.otlp_timeout_s)
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter as GrpcLogExporter
    endpoint, insecure = _grpc_target(cfg)
    return GrpcLogExporter(endpoint=endpoint, insecure=insecure, headers=_headers(cfg), timeout=cfg.otlp_timeout_s)
