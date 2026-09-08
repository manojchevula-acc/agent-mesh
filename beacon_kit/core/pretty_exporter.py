"""PrettySpanExporter / PrettyMetricExporter / PrettyLogExporter — rich-formatted output.

Buffers spans per trace and prints a compact waterfall table once the root span arrives.
Also maintains in-process buffers (traces, metrics, logs) readable via /telemetry/recent.
"""
from __future__ import annotations

import json
import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Sequence

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult

try:
    from opentelemetry.sdk._logs.export import LogExporter, LogExportResult
    _LOGS_SDK_AVAILABLE = True
except ImportError:
    _LOGS_SDK_AVAILABLE = False
    LogExporter = object  # type: ignore[assignment,misc]
    LogExportResult = None  # type: ignore[assignment]

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box as rich_box
    _RICH_AVAILABLE = True
except ImportError:
    _RICH_AVAILABLE = False


# ── Module-level buffers (readable from /telemetry/recent in same process) ───

_recent_traces: list[dict] = []
_recent_metrics: list[dict] = []
_recent_logs: list[dict] = []
_buf_lock = threading.Lock()
_MAX_BUFFER = 10


def get_buffered_traces() -> list[dict]:
    with _buf_lock:
        return list(_recent_traces)


def get_buffered_metrics() -> list[dict]:
    with _buf_lock:
        return list(_recent_metrics)


def get_buffered_logs() -> list[dict]:
    with _buf_lock:
        return list(_recent_logs)


# ── Span helpers ──────────────────────────────────────────────────────────────

def _ms(span: ReadableSpan) -> float:
    if span.start_time and span.end_time:
        return (span.end_time - span.start_time) / 1_000_000
    return 0.0


def _duration_cell(ms: float) -> str:
    color = "green" if ms < 100 else ("yellow" if ms < 500 else "red")
    return f"[{color}]{ms:.0f} ms[/{color}]"


def _status_cell(span: ReadableSpan) -> tuple[str, str]:
    from opentelemetry.trace import StatusCode
    if span.status.status_code == StatusCode.ERROR:
        desc = span.status.description or ""
        short = (desc[:28] + "…") if len(desc) > 28 else desc
        return f"[bold red]ERROR[/bold red] [dim]{short}[/dim]", "ERROR"
    return "[green]OK[/green]", "OK"


def _attrs_cell(span: ReadableSpan) -> str:
    attrs = span.attributes or {}
    kind = span.kind.name
    parts: list[str] = []

    if kind == "SERVER":
        if "service.run_id" in attrs:
            run_id = str(attrs["service.run_id"])
            parts.append(f"run_id=[cyan]{run_id[:16]}[/cyan]")
        if "http.status_code" in attrs:
            parts.append(f"http={attrs['http.status_code']}")

    elif kind == "CLIENT":
        if "tool.latency_ms" in attrs:
            parts.append(f"latency=[yellow]{float(attrs['tool.latency_ms']):.0f}ms[/yellow]")
        has_error = str(attrs.get("tool.output.has_error", "False"))
        color = "red" if has_error == "True" else "green"
        parts.append(f"err=[{color}]{has_error}[/{color}]")

    elif kind == "INTERNAL":
        if "pricing.indicative_price" in attrs:
            parts.append(f"price=[bold white]{float(attrs['pricing.indicative_price']):.4f}[/bold white]")
        if "pricing.rwa_usd" in attrs:
            parts.append(f"rwa={int(attrs['pricing.rwa_usd']):,}")

    return "  ".join(parts)


def _attrs_summary(span: ReadableSpan) -> dict:
    """Extract key attributes as plain dict for the JSON buffer."""
    attrs = span.attributes or {}
    kind = span.kind.name
    out: dict = {}
    if kind == "SERVER":
        for k in ("service.run_id", "http.status_code", "http.route"):
            if k in attrs:
                out[k] = attrs[k]
    elif kind == "CLIENT":
        for k in ("tool.name", "tool.latency_ms", "tool.output.has_error"):
            if k in attrs:
                out[k] = attrs[k]
    elif kind == "INTERNAL":
        for k in ("pricing.indicative_price", "pricing.rwa_usd"):
            if k in attrs:
                out[k] = attrs[k]
    return out


# ── PrettySpanExporter ────────────────────────────────────────────────────────

class PrettySpanExporter(SpanExporter):
    """Accumulates spans by trace ID; flushes as a rich tree table on root span arrival."""

    def __init__(self) -> None:
        self._console = Console(highlight=False) if _RICH_AVAILABLE else None
        self._lock = threading.Lock()
        self._pending: dict[str, list[ReadableSpan]] = defaultdict(list)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        if not _RICH_AVAILABLE:
            return SpanExportResult.SUCCESS

        with self._lock:
            for span in spans:
                tid = format(span.context.trace_id, "032x")
                self._pending[tid].append(span)

            for span in spans:
                if span.parent is None:
                    tid = format(span.context.trace_id, "032x")
                    self._flush_trace(tid)

        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        with self._lock:
            for tid in list(self._pending.keys()):
                self._flush_trace(tid)

    def _flush_trace(self, tid: str) -> None:
        all_spans = self._pending.pop(tid, [])
        if not all_spans:
            return

        by_sid: dict[str, ReadableSpan] = {
            format(s.context.span_id, "016x"): s for s in all_spans
        }
        children: dict[str | None, list[ReadableSpan]] = defaultdict(list)
        root: ReadableSpan | None = None

        for s in all_spans:
            pid = format(s.parent.span_id, "016x") if s.parent else None
            if pid is None or pid not in by_sid:
                root = s
                children[None].append(s)
            else:
                children[pid].append(s)

        for lst in children.values():
            lst.sort(key=lambda s: s.start_time or 0)

        svc_name = (root.resource.attributes or {}).get("service.name", "") if root else ""

        # ── rich console table ──────────────────────────────────────────────
        table = Table(
            box=rich_box.SIMPLE_HEAVY,
            show_header=True,
            header_style="bold cyan",
            title=(
                f"[bold]Layer 02 — Span Trace: {svc_name}[/bold]"
                f"  [dim]trace: {tid[:16]}…[/dim]"
            ),
            title_justify="left",
            padding=(0, 1),
        )
        table.add_column("Span", no_wrap=True, min_width=30)
        table.add_column("Kind", style="dim", width=10)
        table.add_column("Duration", justify="right", width=9)
        table.add_column("Status", width=7)
        table.add_column("Attributes")

        walk_order: list[ReadableSpan] = []

        def walk(span: ReadableSpan, prefix: str, is_last: bool) -> None:
            walk_order.append(span)
            sid = format(span.context.span_id, "016x")
            connector = "└ " if is_last else "├ "
            label = (prefix + connector if (prefix or span.parent) else "") + span.name
            status_markup, _ = _status_cell(span)
            table.add_row(
                label, span.kind.name,
                _duration_cell(_ms(span)), status_markup, _attrs_cell(span),
            )
            child_list = children.get(sid, [])
            child_prefix = prefix + ("  " if is_last else "│ ")
            for i, child in enumerate(child_list):
                walk(child, child_prefix, i == len(child_list) - 1)

        if root:
            walk(root, "", True)

        assert self._console is not None
        self._console.print()
        self._console.print(table)
        self._console.print()

        # ── populate in-process buffer for /telemetry/recent ───────────────
        from opentelemetry.trace import StatusCode
        span_entries = []
        for s in walk_order:
            pid = format(s.parent.span_id, "016x") if s.parent else None
            _, status_plain = _status_cell(s)
            span_entries.append({
                "name": s.name,
                "kind": s.kind.name,
                "duration_ms": round(_ms(s), 2),
                "status": status_plain,
                "span_id": format(s.context.span_id, "016x"),
                "parent_id": pid,
                "attrs": _attrs_summary(s),
            })

        entry = {"trace_id": tid, "service": svc_name, "spans": span_entries}
        with _buf_lock:
            _recent_traces.append(entry)
            if len(_recent_traces) > _MAX_BUFFER:
                _recent_traces.pop(0)


# ── PrettyMetricExporter ──────────────────────────────────────────────────────

class PrettyMetricExporter(MetricExporter):
    """Formats OTEL metrics as a rich table and stores them in the in-process buffer."""

    def __init__(self) -> None:
        super().__init__()
        self._preferred_temporality = {}
        self._preferred_aggregation = {}
        self._console = Console(highlight=False) if _RICH_AVAILABLE else None
        self._last_fingerprint: str | None = None

    def export(self, metrics_data: Any, **kwargs: Any) -> MetricExportResult:
        if not _RICH_AVAILABLE:
            return MetricExportResult.SUCCESS

        rows: list[dict] = []

        try:
            for rm in metrics_data.resource_metrics:
                for sm in rm.scope_metrics:
                    for metric in sm.metrics:
                        row = _extract_metric(metric)
                        if row:
                            rows.append(row)
        except Exception:
            return MetricExportResult.SUCCESS

        if not rows:
            return MetricExportResult.SUCCESS

        # Skip printing if nothing has changed since last flush
        import hashlib as _hl
        fingerprint = _hl.md5(
            json.dumps([r.get("value_str", "") + r.get("name", "") for r in rows], sort_keys=True).encode()
        ).hexdigest()
        if fingerprint == self._last_fingerprint:
            return MetricExportResult.SUCCESS
        self._last_fingerprint = fingerprint

        table = Table(
            box=rich_box.SIMPLE_HEAVY,
            show_header=True,
            header_style="bold magenta",
            title="[bold]Layer 02 — Metrics Snapshot[/bold]",
            title_justify="left",
            padding=(0, 1),
        )
        table.add_column("Metric", no_wrap=True, min_width=35)
        table.add_column("Type", style="dim", width=12)
        table.add_column("Value", justify="right")
        table.add_column("Unit", style="dim", width=6)

        for r in rows:
            table.add_row(r["name"], r["type"], r["value_str"], r.get("unit", ""))

        assert self._console is not None
        self._console.print()
        self._console.print(table)
        self._console.print()

        with _buf_lock:
            _recent_metrics.clear()
            _recent_metrics.extend(rows)

        return MetricExportResult.SUCCESS

    def shutdown(self, timeout_millis: int = 30_000, **kwargs: Any) -> None:
        pass

    def force_flush(self, timeout_millis: int = 10_000) -> bool:
        return True


def _extract_metric(metric: Any) -> dict | None:
    """Pull a display-ready dict from an OTEL metric data point."""
    try:
        from opentelemetry.sdk.metrics.export import (
            HistogramDataPoint, NumberDataPoint,
        )
        data = metric.data
        name = metric.name
        unit = metric.unit or ""

        data_points = list(getattr(data, "data_points", []))
        if not data_points:
            return None

        dp = data_points[0]

        if hasattr(dp, "sum") and hasattr(dp, "count") and hasattr(dp, "min"):
            # Histogram
            count = dp.count
            if count == 0:
                return None
            avg = dp.sum / count
            value_str = (
                f"count={count}  "
                f"avg={avg:.1f}  "
                f"min={dp.min:.1f}  "
                f"max={dp.max:.1f}"
            )
            return {"name": name, "type": "Histogram", "value_str": value_str, "unit": unit, "count": count, "avg": avg, "min": dp.min, "max": dp.max}

        if hasattr(dp, "value"):
            # Counter or Gauge
            val = dp.value
            if val == 0 and "state" not in name:
                return None
            attrs = dict(getattr(dp, "attributes", {}) or {})
            attr_str = "  ".join(f"{k}={v}" for k, v in attrs.items()) if attrs else ""
            type_name = "Gauge" if "state" in name else "Counter"
            # Circuit breaker state label
            if "circuit_breaker.state" in name:
                state_label = {0: "CLOSED", 1: "HALF-OPEN", 2: "OPEN"}.get(int(val), str(val))
                value_str = f"[{'green' if val == 0 else 'yellow' if val == 1 else 'red'}]{val} ({state_label})[/{'green' if val == 0 else 'yellow' if val == 1 else 'red'}]"
            else:
                value_str = f"{val}" + (f"  [dim]{attr_str}[/dim]" if attr_str else "")
            return {"name": name, "type": type_name, "value_str": value_str, "unit": unit, "value": val, "attrs": attrs}

    except Exception:
        pass
    return None


# ── PrettyLogExporter ─────────────────────────────────────────────────────────

class PrettyLogExporter(LogExporter):  # type: ignore[misc]
    """Renders OTEL log records as a rich table and populates the in-process log buffer."""

    def __init__(self) -> None:
        self._console = Console(highlight=False) if _RICH_AVAILABLE else None

    def export(self, batch: Any) -> Any:
        if not _RICH_AVAILABLE or not _LOGS_SDK_AVAILABLE:
            return LogExportResult.SUCCESS if LogExportResult else None

        rows: list[dict] = []
        for log_record in batch:
            try:
                tid = format(log_record.trace_id, "032x") if log_record.trace_id else ""
                sid = format(log_record.span_id, "016x") if log_record.span_id else ""
                ts = datetime.fromtimestamp(
                    log_record.timestamp / 1e9, tz=timezone.utc
                ).strftime("%H:%M:%S.%f")[:-3] if log_record.timestamp else ""
                severity = (log_record.severity_text or "INFO").upper()
                body = str(log_record.body or "")
                logger_name = (log_record.attributes or {}).get("logger.name", "") or ""
                rows.append({
                    "ts": ts,
                    "severity": severity,
                    "logger": str(logger_name),
                    "body": body,
                    "trace_id": tid,
                    "span_id": sid,
                })
            except Exception:
                continue

        if not rows:
            return LogExportResult.SUCCESS

        table = Table(
            box=rich_box.SIMPLE_HEAVY,
            show_header=True,
            header_style="bold green",
            title="[bold]Layer 02 — Structured Logs[/bold]",
            title_justify="left",
            padding=(0, 1),
        )
        table.add_column("Time", style="dim", width=12, no_wrap=True)
        table.add_column("Level", width=7)
        table.add_column("Logger", style="dim", no_wrap=True, max_width=30)
        table.add_column("Message")
        table.add_column("trace_id", style="dim", width=18, no_wrap=True)

        _level_color = {"ERROR": "bold red", "WARNING": "yellow", "WARN": "yellow", "DEBUG": "dim"}
        for r in rows:
            color = _level_color.get(r["severity"], "green")
            table.add_row(
                r["ts"],
                f"[{color}]{r['severity']}[/{color}]",
                r["logger"],
                r["body"],
                r["trace_id"][:16] + "…" if len(r["trace_id"]) > 16 else r["trace_id"],
            )

        assert self._console is not None
        self._console.print()
        self._console.print(table)
        self._console.print()

        with _buf_lock:
            _recent_logs.extend(rows)
            if len(_recent_logs) > _MAX_BUFFER * 5:
                del _recent_logs[:-(_MAX_BUFFER * 5)]

        return LogExportResult.SUCCESS

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True

    def shutdown(self) -> None:
        pass
