"""init_gernas() — one idempotent startup call wires traces, metrics and logs.

Also :func:`safe_set`, the only way SDK code writes attributes: redaction first,
and a failed write is counted, never raised (telemetry must not break the agent).
Dropped spans, log records and attributes are exposed as ``gernas.sdk.*``
metrics (Blueprint §3 Telemetry Platform: "dropped spans/logs").
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.baggage import get_all as baggage_get_all
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk._logs.export import LogRecordExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.trace import ReadableSpan, SpanLimits, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from gernas import redaction
from gernas._version import __version__
from gernas.attributes import Ctx, Metric
from gernas.config import GernasConfig

_log = logging.getLogger("gernas")

# Maximum length of a string attribute value (bounds payload size of every span).
MAX_ATTRIBUTE_LENGTH = 1024

# Baggage entries copied onto every span: execution + identity context that must
# reach remote services (Blueprint §2). Only tokenized identity ever travels.
BAGGAGE_ATTRIBUTES = (
    Ctx.SESSION_ID,
    Ctx.TASK_ID,
    Ctx.TOKENIZED_USER_ID,
    Ctx.PRINCIPAL_TYPE,
    Ctx.TENANT,
    Ctx.DEPARTMENT,
    Ctx.CHANNEL,
)


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


dropped_spans = _Counter()
dropped_logs = _Counter()
dropped_attributes = _Counter()
redactions = _Counter()


def safe_set(span: Any, key: str, value: Any) -> None:
    """Redact, then ``span.set_attribute``; never raises. ``None`` values are skipped."""
    if value is None:
        return
    try:
        if getattr(span, "end_time", None) is not None:
            dropped_attributes.add()
            return
        value, changed = redaction.redact(key, value)
        if changed:
            redactions.add()
            span.set_attribute(Ctx.REDACTION_APPLIED, True)
            if value is redaction.DROP:
                return
        span.set_attribute(key, value)
    except Exception:  # noqa: BLE001 - telemetry must never break the agent
        dropped_attributes.add()


def safe_set_many(span: Any, attributes: Mapping[str, Any]) -> None:
    for key, value in attributes.items():
        safe_set(span, key, value)


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
            dropped_spans.add(len(spans))
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


class _CountingLogExporter(LogRecordExporter):
    """Same as CountingSpanExporter for log records (duck-typed LogRecordExporter)."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def export(self, batch: Sequence[Any]) -> Any:
        try:
            result = self._inner.export(batch)
        except Exception:  # noqa: BLE001
            result = None
        if getattr(result, "name", "") != "SUCCESS":
            dropped_logs.add(len(batch))
        return result

    def shutdown(self) -> None:
        try:
            self._inner.shutdown()
        except Exception:  # noqa: BLE001
            pass

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        try:
            return bool(self._inner.force_flush(timeout_millis))
        except Exception:  # noqa: BLE001
            return False


class _GernasBatchSpanProcessor(BatchSpanProcessor):
    """BatchSpanProcessor that counts spans evicted because the export queue is full."""

    def on_end(self, span: ReadableSpan) -> None:
        try:
            bp = self._batch_processor
            if len(bp._queue) >= bp._max_queue_size:  # noqa: SLF001 - SDK internals, guarded
                dropped_spans.add()
        except Exception:  # noqa: BLE001
            pass
        super().on_end(span)


class _BaggageSpanProcessor(SpanProcessor):
    """Copies the allow-listed baggage entries onto every span at start."""

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        try:
            entries = baggage_get_all(parent_context)
            for key in BAGGAGE_ATTRIBUTES:
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


@dataclass
class GernasHandle:
    config: GernasConfig
    tracer_provider: TracerProvider
    meter_provider: MeterProvider | None
    logger_provider: Any = None
    _shut: bool = field(default=False, repr=False)

    def flush(self, timeout_millis: int = 5_000) -> bool:
        ok = True
        for provider in (self.tracer_provider, self.meter_provider, self.logger_provider):
            if provider is not None:
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
            if provider is not None:
                try:
                    provider.shutdown()
                except Exception:  # noqa: BLE001
                    pass


_lock = threading.Lock()
_handle: GernasHandle | None = None
_tracer: trace.Tracer | None = None


def get_tracer() -> trace.Tracer:
    """Tracer used by every decorator (OTel ProxyTracer until a provider is set)."""
    global _tracer
    if _tracer is None:
        _tracer = trace.get_tracer("gernas", __version__)
    return _tracer


def get_config() -> GernasConfig | None:
    """The active config, or None before init_gernas() — identity is never invented."""
    return _handle.config if _handle is not None else None


def get_handle() -> GernasHandle | None:
    return _handle


def init_gernas(
    config: GernasConfig | None = None,
    *,
    service_name: str | None = None,
    span_exporter: SpanExporter | None = None,
    metric_reader: Any = None,
) -> GernasHandle:
    """Initialise the SDK from ``GERNAS_*`` environment variables. Idempotent.

    ``span_exporter`` / ``metric_reader`` replace the configured exporters (used by
    tests and ``gernas doctor``). Raises :class:`gernas.GernasConfigError` on missing
    mandatory configuration.
    """
    global _handle
    with _lock:
        if _handle is not None:
            return _handle
        cfg = config or GernasConfig.from_env(service_name=service_name)
        from gernas.exporters import build_log_exporter, build_metric_exporter, build_span_exporter

        resource = cfg.resource
        exporter = CountingSpanExporter(span_exporter or build_span_exporter(cfg))
        existing = trace.get_tracer_provider()
        if isinstance(existing, TracerProvider):
            tracer_provider = existing
            _log.warning("gernas: an SDK TracerProvider already exists; attaching Gernas processors to it")
        else:
            tracer_provider = TracerProvider(
                resource=resource,
                sampler=ParentBased(root=TraceIdRatioBased(cfg.sampling_rate)),
                span_limits=SpanLimits(max_attribute_length=MAX_ATTRIBUTE_LENGTH),
            )
        tracer_provider.add_span_processor(_BaggageSpanProcessor())
        tracer_provider.add_span_processor(
            SimpleSpanProcessor(exporter) if span_exporter is not None else _GernasBatchSpanProcessor(exporter)
        )
        if tracer_provider is not existing:
            trace.set_tracer_provider(tracer_provider)
        set_global_textmap(CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()]))

        meter_provider = None
        try:
            reader = metric_reader or PeriodicExportingMetricReader(
                build_metric_exporter(cfg), export_interval_millis=cfg.metric_interval_ms
            )
            meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
            metrics.set_meter_provider(meter_provider)
        except Exception as exc:  # noqa: BLE001
            _log.warning("gernas: metrics disabled: %s", exc)

        logger_provider = _init_logs(cfg, resource, build_log_exporter)
        _handle = GernasHandle(cfg, tracer_provider, meter_provider, logger_provider)
        _register_sdk_metrics()
        return _handle


def _init_logs(cfg: GernasConfig, resource: Any, build_log_exporter: Any) -> Any:
    try:
        from opentelemetry._logs import set_logger_provider
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

        from gernas.logs import GernasLogHandler

        provider = LoggerProvider(resource=resource)
        provider.add_log_record_processor(BatchLogRecordProcessor(_CountingLogExporter(build_log_exporter(cfg))))
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
    try:
        from opentelemetry.metrics import CallbackOptions, Observation

        meter = metrics.get_meter("gernas", __version__)
        for name, counter, unit in (
            (Metric.SDK_DROPPED_SPANS, dropped_spans, "{span}"),
            (Metric.SDK_DROPPED_LOGS, dropped_logs, "{log}"),
            (Metric.SDK_DROPPED_ATTRIBUTES, dropped_attributes, "{attribute}"),
            (Metric.SDK_REDACTIONS, redactions, "{attribute}"),
        ):

            def callback(_: CallbackOptions, c: _Counter = counter) -> list[Observation]:
                return [Observation(c.value)]

            meter.create_observable_counter(name, [callback], unit=unit)
    except Exception:  # noqa: BLE001
        pass
