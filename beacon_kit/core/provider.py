"""setup_telemetry() — single entry point to wire up the full OTEL stack."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased, ParentBased
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.baggage.propagation import W3CBaggagePropagator

from beacon_kit.core.exporters import build_span_exporter, build_metric_exporter, build_log_exporter

if TYPE_CHECKING:
    from opentelemetry.trace import Tracer
    from opentelemetry.metrics import Meter
    from beacon_kit.core.config import TelemetryConfig


@dataclass
class TelemetryHandle:
    tracer: "Tracer"
    meter: "Meter"
    _tracer_provider: TracerProvider
    _meter_provider: MeterProvider
    _logger_provider: Any = field(default=None)

    def shutdown(self) -> None:
        self._tracer_provider.shutdown()
        self._meter_provider.shutdown()
        if self._logger_provider is not None:
            try:
                self._logger_provider.shutdown()
            except Exception:
                pass


def flush_telemetry(handle: TelemetryHandle, timeout_millis: int = 5000) -> None:
    """Force-flush all pending spans and metrics before process exit or checkpoint.

    Logs a warning if the flush times out rather than raising so callers can
    always proceed with shutdown.
    """
    _log = logging.getLogger(__name__)
    try:
        handle._tracer_provider.force_flush(timeout_millis=timeout_millis)
    except Exception as exc:
        _log.warning("beacon_kit: tracer flush timed out or failed: %s", exc)
    try:
        handle._meter_provider.force_flush(timeout_millis=timeout_millis)
    except Exception as exc:
        _log.warning("beacon_kit: meter flush timed out or failed: %s", exc)


def setup_telemetry(config: "TelemetryConfig") -> TelemetryHandle:
    resource = Resource.create(
        {
            SERVICE_NAME: config.service_name,
            SERVICE_VERSION: config.service_version,
            "deployment.environment": config.environment,
            "k8s.pod.name": config.pod,
        }
    )

    span_exporter = build_span_exporter(config)
    sampler = ParentBased(root=TraceIdRatioBased(config.sampling_rate))

    tracer_provider = TracerProvider(resource=resource, sampler=sampler)
    if config.exporter_type == "pretty":
        # SimpleSpanProcessor exports each span immediately — no 5-second batch delay
        tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    else:
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))

    if config.enable_baggage_propagation:
        from beacon_kit.propagation.baggage import BaggageSpanProcessor
        tracer_provider.add_span_processor(BaggageSpanProcessor())

    trace.set_tracer_provider(tracer_provider)

    metric_exporter = build_metric_exporter(config)
    # In pretty mode flush metrics every 5 s so they appear shortly after each request
    metric_interval = 5_000 if config.exporter_type == "pretty" else 30_000
    metric_readers = [PeriodicExportingMetricReader(metric_exporter, export_interval_millis=metric_interval)]

    # ── Secondary file processors (dual-export: primary backend + local JSONL) ─
    # Build file exporters independently so a metric reader failure doesn't
    # prevent span or log file export. File_output_dir must be set; exporter_type
    # "file" already writes to files as the primary (no secondary needed), and
    # "off" discards everything.
    _wants_file_secondary = (
        bool(config.file_output_dir)
        and config.exporter_type not in ("file", "off")
    )
    _file_span_exp = _file_log_exp = None
    if _wants_file_secondary:
        try:
            from beacon_kit.core.file_exporters import FileSpanExporter, FileMetricExporter, FileLogExporter
            _file_span_exp = FileSpanExporter(config.file_path("traces"))
            _file_log_exp = FileLogExporter(config.file_path("logs"))
        except Exception:
            pass
        try:
            _file_metric_exp = FileMetricExporter(config.file_path("metrics"))
            metric_readers.append(
                PeriodicExportingMetricReader(_file_metric_exp, export_interval_millis=15_000)
            )
        except Exception:
            pass  # metric file failure never kills traces or logs

    meter_provider = MeterProvider(resource=resource, metric_readers=metric_readers)
    metrics.set_meter_provider(meter_provider)

    # Add secondary span processor after MeterProvider is created
    if _file_span_exp is not None:
        try:
            tracer_provider.add_span_processor(SimpleSpanProcessor(_file_span_exp))
        except Exception:
            pass

    # W3C TraceContext + W3C Baggage as global propagator
    set_global_textmap(
        CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])
    )

    tracer = trace.get_tracer(config.service_name, config.service_version)
    meter = metrics.get_meter(config.service_name, config.service_version)

    # ── OTEL Logs (third pillar) ───────────────────────────────────────────────
    logger_provider = None
    try:
        from opentelemetry._logs import set_logger_provider
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

        log_exporter = build_log_exporter(config)
        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))

        # Secondary file log processor for dual-export
        if _file_log_exp is not None:
            try:
                logger_provider.add_log_record_processor(BatchLogRecordProcessor(_file_log_exp))
            except Exception:
                pass

        set_logger_provider(logger_provider)

        # Bridge Python's logging module to OTEL so @log_calls and manual
        # logging.getLogger(__name__).info() calls produce OTEL log records
        # with trace_id / span_id automatically injected.
        # Covers beacon_kit internals + all mesh/agent namespaces used in this project.
        otel_handler = LoggingHandler(level=logging.DEBUG, logger_provider=logger_provider)
        for ns in ("beacon_kit", "simple_project", "mesh", "src", "agent_mesh"):
            ns_logger = logging.getLogger(ns)
            if not any(isinstance(h, LoggingHandler) for h in ns_logger.handlers):
                ns_logger.addHandler(otel_handler)
                ns_logger.setLevel(logging.DEBUG)
    except ImportError:
        pass  # OTEL logs SDK unavailable — traces and metrics still work

    return TelemetryHandle(
        tracer=tracer,
        meter=meter,
        _tracer_provider=tracer_provider,
        _meter_provider=meter_provider,
        _logger_provider=logger_provider,
    )
