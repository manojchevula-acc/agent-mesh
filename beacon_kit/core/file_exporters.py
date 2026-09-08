"""File-based OTEL exporters — write spans, metrics, and log records to local JSONL files.

Each exporter opens a single file at init (append mode) and writes one JSON line
per record/batch-item throughout the process lifetime. Files are auto-created including
parent directories. All methods are crash-safe — exceptions are swallowed so a missing
directory or full disk never kills the main process.

Typical usage (via TelemetryConfig.from_env() + setup_telemetry):
    OTEL_FILE_OUTPUT_DIR=../observability
    OBS_PROFILE=grafana_and_file   # file export alongside Grafana
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Sequence

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.metrics.export import (
    MetricExporter,
    MetricExportResult,
    MetricsData,
)


def _open(path: str):
    """Create parent dirs and open path in UTF-8 append mode."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return open(path, "a", encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


# ── Span exporter ─────────────────────────────────────────────────────────────

class FileSpanExporter(SpanExporter):
    """Appends every finished span as a JSONL line to *path*."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        try:
            self._fh = _open(path)
        except Exception:
            self._fh = None

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        if self._fh is None:
            return SpanExportResult.SUCCESS
        try:
            with self._lock:
                for span in spans:
                    ctx = span.context
                    record = {
                        "ts": _now_iso(),
                        "trace_id": f"0x{ctx.trace_id:032x}" if ctx else None,
                        "span_id": f"0x{ctx.span_id:016x}" if ctx else None,
                        "parent_span_id": (
                            f"0x{span.parent.span_id:016x}" if span.parent else None
                        ),
                        "name": span.name,
                        "kind": span.kind.name if span.kind else None,
                        "start_ms": span.start_time / 1e6 if span.start_time else None,
                        "duration_ms": (
                            round((span.end_time - span.start_time) / 1e6, 3)
                            if span.end_time and span.start_time
                            else None
                        ),
                        "status": span.status.status_code.name if span.status else None,
                        "attributes": dict(span.attributes) if span.attributes else {},
                        "events": [
                            {
                                "name": e.name,
                                "ts": _now_iso(),
                                "attributes": dict(e.attributes) if e.attributes else {},
                            }
                            for e in (span.events or [])
                        ],
                        "resource": (
                            dict(span.resource.attributes)
                            if span.resource and span.resource.attributes
                            else {}
                        ),
                    }
                    self._fh.write(json.dumps(record, default=str) + "\n")
                self._fh.flush()
        except Exception:
            pass
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        try:
            if self._fh:
                self._fh.flush()
                self._fh.close()
        except Exception:
            pass


# ── Metric exporter ───────────────────────────────────────────────────────────

class FileMetricExporter(MetricExporter):
    """Appends one JSONL line per data point on every periodic flush."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        try:
            self._fh = _open(path)
        except Exception:
            self._fh = None

    def export(self, metrics_data: MetricsData, **_) -> MetricExportResult:
        if self._fh is None:
            return MetricExportResult.SUCCESS
        try:
            ts = _now_iso()
            with self._lock:
                for rm in metrics_data.resource_metrics:
                    for sm in rm.scope_metrics:
                        for metric in sm.metrics:
                            data = metric.data
                            data_points = getattr(data, "data_points", [])
                            kind = type(data).__name__.lower().replace("gauge", "gauge").replace("sum", "counter").replace("histogram", "histogram")
                            for dp in data_points:
                                attrs = dict(dp.attributes) if dp.attributes else {}
                                # Resolve the scalar value depending on point type
                                if hasattr(dp, "value"):
                                    value = dp.value
                                elif hasattr(dp, "sum"):
                                    value = {"sum": dp.sum, "count": dp.count}
                                else:
                                    value = None
                                record = {
                                    "ts": ts,
                                    "name": metric.name,
                                    "description": metric.description,
                                    "kind": kind,
                                    "unit": metric.unit,
                                    "attributes": attrs,
                                    "value": value,
                                }
                                self._fh.write(json.dumps(record, default=str) + "\n")
                self._fh.flush()
        except Exception:
            pass
        return MetricExportResult.SUCCESS

    def force_flush(self, timeout_millis: int = 10_000) -> bool:
        return True

    def shutdown(self, timeout_millis: int = 30_000, **_) -> None:
        try:
            if self._fh:
                self._fh.flush()
                self._fh.close()
        except Exception:
            pass


# ── Log exporter ──────────────────────────────────────────────────────────────

class FileLogExporter:
    """Appends every OTEL log record as a JSONL line.

    Implements the opentelemetry.sdk._logs.export.LogExporter interface without
    importing it at module level so this file works even if the OTEL logs SDK
    extra is not installed.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        try:
            self._fh = _open(path)
        except Exception:
            self._fh = None

    def export(self, batch) -> int:  # returns LogExportResult.SUCCESS == 0
        if self._fh is None:
            return 0
        try:
            with self._lock:
                for lr in batch:
                    # Some OTEL SDK versions wrap the actual log record in a
                    # ReadableLogRecord(log_record=...) container; unwrap if needed.
                    inner = getattr(lr, "log_record", lr)
                    body = getattr(inner, "body", None) or getattr(lr, "body", None)
                    if body is None or not isinstance(body, (str, int, float, bool)):
                        body = str(body) if body is not None else ""
                    trace_id_int = getattr(inner, "trace_id", None) or getattr(lr, "trace_id", 0)
                    span_id_int  = getattr(inner, "span_id",  None) or getattr(lr, "span_id",  0)
                    trace_id = f"0x{trace_id_int:032x}" if trace_id_int else None
                    span_id  = f"0x{span_id_int:016x}"  if span_id_int  else None
                    severity = (
                        getattr(inner, "severity_text", None)
                        or getattr(lr, "severity_text", None)
                        or "INFO"
                    )
                    logger_name = ""
                    scope = getattr(lr, "instrumentation_scope", None)
                    if scope:
                        logger_name = getattr(scope, "name", "") or ""
                    attrs = {}
                    raw_attrs = getattr(inner, "attributes", None) or getattr(lr, "attributes", None)
                    if raw_attrs:
                        attrs = dict(raw_attrs)
                    record = {
                        "ts": _now_iso(),
                        "level": severity,
                        "logger": logger_name,
                        "trace_id": trace_id,
                        "span_id": span_id,
                        "msg": body,
                        "attributes": attrs,
                    }
                    self._fh.write(json.dumps(record, default=str) + "\n")
                self._fh.flush()
        except Exception:
            pass
        return 0  # LogExportResult.SUCCESS

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True

    def shutdown(self) -> None:
        try:
            if self._fh:
                self._fh.flush()
                self._fh.close()
        except Exception:
            pass
