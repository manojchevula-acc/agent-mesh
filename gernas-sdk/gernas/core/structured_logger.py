"""StructuredLogger — OTel-backed structured logging for the Gernas SDK.

Emits log records as proper OTel LogRecord objects with structured attributes
(every **kwarg becomes a searchable field in the JSONL output), auto-attaching
trace_id, span_id, and service.name from the current OTel context.

Usage:
    from gernas import get_logger
    log = get_logger("my_service")
    log.info("Agent responded", agent="rag", latency_ms=310, tokens=850)

The JSONL record produced by FileLogExporter will have ``agent``, ``latency_ms``,
and ``tokens`` as top-level fields, enabling instant search and filtering in the
HTML report and any downstream log tool.
"""
from __future__ import annotations

import logging
import traceback
from datetime import datetime, timezone
from typing import Any

from gernas.utils.sanitizer import scrub_pii

try:
    from opentelemetry import trace
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry._logs import get_logger_provider, std_to_otel
    from opentelemetry._logs import Logger as OtelLogger
    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False

_LEVEL_MAP = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}

_OTEL_SEV_MAP = {
    logging.DEBUG: 5,    # DEBUG
    logging.INFO: 9,     # INFO
    logging.WARNING: 13, # WARN
    logging.ERROR: 17,   # ERROR
    logging.CRITICAL: 21,# FATAL
}


class StructuredLogger:
    """Thin logger that emits OTel LogRecord objects with structured attributes.

    Falls back to standard Python logging when no OTel LoggerProvider is
    configured (e.g. during tests or before init_gernas() runs).
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._stdlib = logging.getLogger(name)
        self._otel: OtelLogger | None = self._resolve_otel_logger(name)

    @staticmethod
    def _resolve_otel_logger(name: str) -> "OtelLogger | None":
        if not _OTEL_AVAILABLE:
            return None
        try:
            provider = get_logger_provider()
            if isinstance(provider, LoggerProvider):
                return provider.get_logger(name)
        except Exception:
            pass
        return None

    def _emit(self, level: int, msg: str, exc_info: bool = False, **fields: Any) -> None:
        ts_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)

        # Collect structured context from active OTel span
        ctx_fields: dict[str, Any] = {}
        if _OTEL_AVAILABLE:
            try:
                span = trace.get_current_span()
                sctx = span.get_span_context()
                if sctx and sctx.is_valid:
                    ctx_fields["trace_id"] = format(sctx.trace_id, "032x")
                    ctx_fields["span_id"] = format(sctx.span_id, "016x")
            except Exception:
                pass

        if exc_info:
            fields["exception"] = traceback.format_exc()

        # PDPL: free-text field values are PII-scrubbed before they leave the process
        all_fields = {**ctx_fields, **{k: scrub_pii(v) if isinstance(v, str) else v for k, v in fields.items()}}

        if self._otel is not None:
            try:
                from opentelemetry import context as otel_context
                from opentelemetry._logs.severity import SeverityNumber
                self._otel.emit(
                    timestamp=ts_ns, observed_timestamp=ts_ns, context=otel_context.get_current(),
                    severity_text=logging.getLevelName(level),
                    severity_number=SeverityNumber(_OTEL_SEV_MAP.get(level, 9)),
                    body=scrub_pii(msg), attributes=all_fields or None,
                )
                return
            except Exception:
                pass  # fall through to stdlib

        # Stdlib fallback — emit a log record with structured fields as extras
        extra = {"structured": all_fields}
        self._stdlib.log(level, msg, extra=extra)

    def debug(self, msg: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, msg, **fields)

    def info(self, msg: str, **fields: Any) -> None:
        self._emit(logging.INFO, msg, **fields)

    def warning(self, msg: str, **fields: Any) -> None:
        self._emit(logging.WARNING, msg, **fields)

    def error(self, msg: str, exc_info: bool = False, **fields: Any) -> None:
        self._emit(logging.ERROR, msg, exc_info=exc_info, **fields)

    def critical(self, msg: str, exc_info: bool = False, **fields: Any) -> None:
        self._emit(logging.CRITICAL, msg, exc_info=exc_info, **fields)

    # Alias
    warn = warning


_loggers: dict[str, StructuredLogger] = {}


def get_logger(name: str) -> StructuredLogger:
    """Return a cached StructuredLogger for the given name.

    Example::

        log = get_logger("my_agent")
        log.info("Request handled", user="alice", latency_ms=320, tokens=850)
    """
    if name not in _loggers:
        _loggers[name] = StructuredLogger(name)
    return _loggers[name]
