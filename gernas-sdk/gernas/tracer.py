"""init_gernas() — one idempotent startup call wires traces, metrics and logs.

Also home of :func:`safe_set`, the only way SDK code writes span attributes:
it runs the PDPL redaction hook first and never lets an attribute failure reach
the agent (principle 4). Dropped attributes and spans are counted, never raised.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from opentelemetry import metrics, trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from gernas import redaction
from gernas._version import __version__
from gernas.attributes import Ctx, Metric
from gernas.config import GernasConfig

_log = logging.getLogger("gernas")

# ── drop counters (thread-safe, process-wide) ────────────────────────────────


class _Counter:
    __slots__ = ("_value", "_lock")

    def __init__(self) -> None:
        self._value = 0
        self._lock = threading.Lock()

    def add(self, n: int = 1) -> None:
        with self._lock:
            self._value += n

    @property
    def value(self) -> int:
        return self._value


_dropped_attributes = _Counter()
_dropped_spans = _Counter()
_redactions = _Counter()


def get_dropped_attribute_count() -> int:
    """Attribute writes that failed and were swallowed by :func:`safe_set`."""
    return _dropped_attributes.value


def get_dropped_span_count() -> int:
    """Spans that could not be exported (Collector unreachable, queue full)."""
    return _dropped_spans.value


def get_redaction_count() -> int:
    return _redactions.value


# ── safe attribute writes ────────────────────────────────────────────────────

_SKIP = (None,)


def safe_set(span: Any, key: str, value: Any) -> None:
    """Redact, then ``span.set_attribute(key, value)``; never raises.

    ``None`` values are skipped. A failed write (or a write to an already-ended
    span) increments ``gernas.sdk.dropped_attributes``.
    """
    if value is None:
        return
    try:
        if getattr(span, "end_time", None) is not None:
            _dropped_attributes.add()
            return
        value, changed = redaction.redact(key, value)
        if changed:
            _redactions.add()
            if value is redaction.DROP:
                span.set_attribute(Ctx.REDACTION_APPLIED, True)
                return
            span.set_attribute(Ctx.REDACTION_APPLIED, True)
        span.set_attribute(key, value)
    except Exception:  # noqa: BLE001 - telemetry must never break the agent
        _dropped_attributes.add()


def safe_set_many(span: Any, attributes: Mapping[str, Any] | Iterable[tuple[str, Any]]) -> None:
    items = attributes.items() if isinstance(attributes, Mapping) else attributes
    for key, value in items:
        safe_set(span, key, value)


# ── exporter / processor wrappers that count drops ───────────────────────────


class CountingSpanExporter(SpanExporter):
    """Wraps the real exporter; failed or raising exports count as dropped spans."""

    def __init__(self, inner: SpanExporter) -> None:
        self._inner = inner

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            result = self._inner.export(spans)
        except Exception:  # noqa: BLE001
            result = SpanExportResult.FAILURE
        if result is not SpanExportResult.SUCCESS:
            _dropped_spans.add(len(spans))
        return result

    def shutdown(self) -> None:
        try:
            self._inner.shutdown()
        except Exception:  # noqa: BLE001
            pass

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        try:
            return self._inner.force_flush(timeout_millis)
        except Exception:  # noqa: BLE001
            return False


class GernasBatchSpanProcessor(BatchSpanProcessor):
    """BatchSpanProcessor that counts spans evicted because the queue was full."""

    def on_end(self, span: ReadableSpan) -> None:
        try:
            bp = self._batch_processor
            if len(bp._queue) >= bp._max_queue_size:  # noqa: SLF001 - SDK internals, guarded
                _dropped_spans.add()
        except Exception:  # noqa: BLE001
            pass
        super().on_end(span)


# ── baggage → span attributes (allow-listed keys only) ───────────────────────


class BaggageSpanProcessor(SpanProcessor):
    """Copies allow-listed W3C baggage entries onto every span at start.

    Only keys in ``GernasConfig.baggage_attributes`` are copied, so user identity
    carried in baggage never becomes a span attribute (PDPL).
    """

    def __init__(self, keys: Iterable[str]) -> None:
        self._keys = tuple(keys)

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        from opentelemetry import baggage

        try:
            entries = baggage.get_all(parent_context)
            for key in self._keys:
                value = entries.get(key)
                if value is not None:
                    safe_set(span, key, value)
        except Exception:  # noqa: BLE001
            pass

    def on_end(self, span: ReadableSpan) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


# ── handle + init ────────────────────────────────────────────────────────────


@dataclass
class GernasHandle:
    config: GernasConfig
    tracer_provider: TracerProvider
    meter_provider: MeterProvider | None
    logger_provider: Any = None
    span_exporter: SpanExporter | None = None
    _shut: bool = field(default=False, repr=False)

    @property
    def tracer(self) -> trace.Tracer:
        return get_tracer()

    @property
    def meter(self) -> metrics.Meter:
        return metrics.get_meter("gernas", __version__)

    @property
    def dropped_spans(self) -> int:
        return get_dropped_span_count()

    @property
    def dropped_attributes(self) -> int:
        return get_dropped_attribute_count()

    def flush(self, timeout_millis: int = 5_000) -> bool:
        ok = True
        for provider in (self.tracer_provider, self.meter_provider, self.logger_provider):
            if provider is None:
                continue
            try:
                ok = bool(provider.force_flush(timeout_millis=timeout_millis)) and ok
            except Exception as exc:  # noqa: BLE001
                _log.warning("gernas: flush failed: %s", exc)
                ok = False
        return ok

    def shutdown(self) -> None:
        if self._shut:
            return
        self._shut = True
        for provider in (self.tracer_provider, self.meter_provider, self.logger_provider):
            if provider is None:
                continue
            try:
                provider.shutdown()
            except Exception:  # noqa: BLE001
                pass


_lock = threading.Lock()
_handle: GernasHandle | None = None
_config: GernasConfig | None = None


_tracer: trace.Tracer | None = None


def get_tracer() -> trace.Tracer:
    """Tracer used by every decorator.

    Cached: before init_gernas() this is OTel's ProxyTracer, which transparently
    delegates to the real provider once one is set.
    """
    global _tracer
    if _tracer is None:
        _tracer = trace.get_tracer("gernas", __version__)
    return _tracer


def get_config() -> GernasConfig:
    """Active config: the one passed to init_gernas(), else built from env lazily."""
    global _config
    if _config is None:
        _config = GernasConfig.from_env(require_endpoint=False)
        redaction.set_capture_payloads(_config.capture_payloads)
    return _config


def get_handle() -> GernasHandle | None:
    return _handle


def is_initialized() -> bool:
    return _handle is not None


def init_gernas(
    config: GernasConfig | None = None,
    *,
    service_name: str | None = None,
    span_exporter: SpanExporter | None = None,
) -> GernasHandle:
    """Initialise the SDK from environment settings alone. Idempotent.

    A second call returns the existing handle without creating a new provider.
    ``span_exporter`` overrides the configured exporter (tests, doctor).
    """
    global _handle, _config
    with _lock:
        if _handle is not None:
            return _handle
        cfg = config or GernasConfig.from_env(service_name=service_name)
        if service_name and config is not None:
            cfg.service_name = service_name
        _config = cfg
        redaction.set_capture_payloads(cfg.capture_payloads)

        from gernas.exporters import build_log_exporter, build_metric_exporter, build_span_exporter

        resource = cfg.resource
        exporter = span_exporter or build_span_exporter(cfg)
        counting = CountingSpanExporter(exporter)
        sampler = ParentBased(root=TraceIdRatioBased(cfg.sampling_rate))

        existing = trace.get_tracer_provider()
        if isinstance(existing, TracerProvider):
            tracer_provider = existing  # someone set an SDK provider first: join it
            _log.warning("gernas: an SDK TracerProvider already exists; attaching Gernas processors to it")
        else:
            tracer_provider = TracerProvider(resource=resource, sampler=sampler)
        tracer_provider.add_span_processor(BaggageSpanProcessor(cfg.baggage_attributes))
        if cfg.exporter in ("pretty", "memory") or span_exporter is not None:
            tracer_provider.add_span_processor(SimpleSpanProcessor(counting))
        else:
            tracer_provider.add_span_processor(GernasBatchSpanProcessor(counting))
        if cfg.file_output_dir and cfg.exporter not in ("file", "off", "memory"):
            try:
                from gernas.core.file_exporters import FileSpanExporter
                tracer_provider.add_span_processor(SimpleSpanProcessor(FileSpanExporter(cfg.file_path("traces"))))
            except Exception as exc:  # noqa: BLE001
                _log.warning("gernas: secondary file span sink disabled: %s", exc)
        if tracer_provider is not existing:
            trace.set_tracer_provider(tracer_provider)

        set_global_textmap(CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()]))

        meter_provider = None
        try:
            readers = [PeriodicExportingMetricReader(build_metric_exporter(cfg),
                                                     export_interval_millis=cfg.metric_interval_ms)]
            if cfg.file_output_dir and cfg.exporter not in ("file", "off", "memory"):
                from gernas.core.file_exporters import FileMetricExporter
                readers.append(PeriodicExportingMetricReader(FileMetricExporter(cfg.file_path("metrics")),
                                                             export_interval_millis=15_000))
            meter_provider = MeterProvider(resource=resource, metric_readers=readers)
            metrics.set_meter_provider(meter_provider)
        except Exception as exc:  # noqa: BLE001
            _log.warning("gernas: metrics disabled: %s", exc)

        logger_provider = _init_logs(cfg, resource, build_log_exporter)

        _handle = GernasHandle(cfg, tracer_provider, meter_provider, logger_provider, exporter)
        _register_sdk_metrics()
        return _handle


def _init_logs(cfg: GernasConfig, resource: Any, build_log_exporter) -> Any:
    try:
        from opentelemetry._logs import set_logger_provider
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, SimpleLogRecordProcessor

        from gernas.logconfig.otel_handler import GernasLogHandler
    except ImportError:
        return None
    try:
        provider = LoggerProvider(resource=resource)
        provider.add_log_record_processor(BatchLogRecordProcessor(build_log_exporter(cfg)))
        if cfg.file_output_dir and cfg.exporter not in ("file", "off", "memory"):
            from gernas.core.file_exporters import FileLogExporter
            provider.add_log_record_processor(SimpleLogRecordProcessor(FileLogExporter(cfg.file_path("logs"))))
        set_logger_provider(provider)
        handler = GernasLogHandler(provider, level=logging.INFO)
        for ns in cfg.log_namespaces:
            ns_logger = logging.getLogger(ns)
            if not any(isinstance(h, GernasLogHandler) for h in ns_logger.handlers):
                ns_logger.addHandler(handler)
            if ns_logger.level == logging.NOTSET or ns_logger.level > logging.INFO:
                ns_logger.setLevel(logging.INFO)
        return provider
    except Exception as exc:  # noqa: BLE001
        _log.warning("gernas: logs disabled: %s", exc)
        return None


def _register_sdk_metrics() -> None:
    """Expose the drop counters as observable counters so the Collector sees them."""
    try:
        from opentelemetry.metrics import CallbackOptions, Observation

        meter = metrics.get_meter("gernas", __version__)

        def _obs(counter: _Counter):
            def cb(_: CallbackOptions):
                return [Observation(counter.value)]
            return cb

        meter.create_observable_counter(Metric.DROPPED_ATTRIBUTES, [_obs(_dropped_attributes)], unit="{attribute}")
        meter.create_observable_counter(Metric.DROPPED_SPANS, [_obs(_dropped_spans)], unit="{span}")
        meter.create_observable_counter(Metric.REDACTIONS, [_obs(_redactions)], unit="{attribute}")
    except Exception:  # noqa: BLE001
        pass


def flush_gernas(timeout_millis: int = 5_000) -> bool:
    return _handle.flush(timeout_millis) if _handle is not None else True


def shutdown_gernas() -> None:
    if _handle is not None:
        _handle.shutdown()
