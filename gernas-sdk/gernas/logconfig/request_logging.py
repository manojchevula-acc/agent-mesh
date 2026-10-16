"""TraceContextFilter and setup_request_logging — Layer 1 Infrastructure.

Stamps every log record with trace_id, span_id, and request_id (from W3C Baggage),
and wires up rotating file + console handlers.
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from opentelemetry import trace, baggage

from gernas.attributes import Ctx


class TraceContextFilter(logging.Filter):
    """Inject OTEL trace_id, span_id, and baggage request_id into every log record."""

    _BAGGAGE_REQUEST_ID_KEYS = (Ctx.REQUEST_ID, "fab.request_id", "request_id")

    def filter(self, record: logging.LogRecord) -> bool:
        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx and ctx.trace_id:
            record.trace_id = format(ctx.trace_id, "032x")
            record.span_id = format(ctx.span_id, "016x")
        else:
            record.trace_id = "0" * 32
            record.span_id = "0" * 16

        request_id = ""
        for key in self._BAGGAGE_REQUEST_ID_KEYS:
            request_id = baggage.get_baggage(key) or ""
            if request_id:
                break
        record.request_id = request_id
        return True


_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | trace=%(trace_id)s span=%(span_id)s req=%(request_id)s | %(message)s"


def setup_request_logging(
    log_dir: str | Path = "./logs",
    service_name: str = "gernas",
    level: int = logging.INFO,
) -> logging.Logger:
    """Set up a root logger with rotating file + console handlers, both stamped with trace context.

    Returns the configured root logger.
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger(service_name)
    root.setLevel(level)

    if root.handlers:
        return root

    ctx_filter = TraceContextFilter()
    formatter = logging.Formatter(_LOG_FORMAT)

    # Rotating file handler — 10 MB per file, keep 5 backups
    file_handler = logging.handlers.RotatingFileHandler(
        log_path / f"{service_name}.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(ctx_filter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(ctx_filter)

    root.addHandler(file_handler)
    root.addHandler(console_handler)
    return root
