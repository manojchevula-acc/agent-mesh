"""Centralized application logging for the agent mesh.

A single, durable, rotating log strategy that captures every significant log
event across all layers (DEBUG..CRITICAL) and correlates each record with the
active OpenTelemetry trace/span so logs and traces line up in any backend.

Design
------
- One rotating file sink at ``Config.LOG_FILE`` (durable; survives restarts).
- One console sink at ``Config.LOG_LEVEL`` for local dev.
- ``TraceContextFilter`` injects ``trace_id`` / ``span_id`` / ``parent_span_id``
  onto every record from the current span context (no-op safe if OTel is off).
- Named logger categories let each layer log independently:
  ``mesh.agent``, ``mesh.workflow``, ``mesh.tools``, ``mesh.a2a``, ``mesh.mcp``,
  ``mesh.transport``, ``mesh.approvals``, ``mesh.system``.
- Optional JSON formatter (``Config.LOG_JSON``) for structured ingestion.

This module is import-safe and never raises during configuration; observability
must never crash the application.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
from typing import Any

from src.config import Config

# Logger category names — import these where you log so categories stay consistent.
CAT_AGENT = "mesh.agent"
CAT_WORKFLOW = "mesh.workflow"
CAT_TOOLS = "mesh.tools"
CAT_A2A = "mesh.a2a"
CAT_MCP = "mesh.mcp"
CAT_TRANSPORT = "mesh.transport"
CAT_APPROVALS = "mesh.approvals"
CAT_SYSTEM = "mesh.system"

_CONFIGURED = False


class TraceContextFilter(logging.Filter):
    """Stamps every log record with the active trace/span ids.

    Reads the current OpenTelemetry span context so file/console records can be
    correlated with distributed traces. Degrades gracefully to ``-`` when no
    span is active or OTel is not installed/configured.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        trace_id = span_id = parent_span_id = request_id = "-"
        try:
            from opentelemetry import trace, baggage  # local import: optional dep

            span = trace.get_current_span()
            ctx = span.get_span_context() if span is not None else None
            if ctx is not None and getattr(ctx, "is_valid", False):
                trace_id = format(ctx.trace_id, "032x")
                span_id = format(ctx.span_id, "016x")
            # parent span id is not directly exposed; surface it from baggage if set
            parent_span_id = baggage.get_baggage("parent_span_id") or "-"
            # request_id propagated as W3C baggage (set by orchestrator entry point)
            request_id = baggage.get_baggage("fab.request_id") or "-"
        except Exception:
            pass
        record.trace_id = trace_id
        record.span_id = span_id
        record.parent_span_id = parent_span_id
        record.request_id = request_id
        return True


class _JsonFormatter(logging.Formatter):
    """Compact JSON formatter for structured log ingestion (prod option)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": getattr(record, "trace_id", "-"),
            "span_id": getattr(record, "span_id", "-"),
            "parent_span_id": getattr(record, "parent_span_id", "-"),
            "request_id": getattr(record, "request_id", "-"),
            "msg": record.getMessage(),
        }
        # Surface common correlation extras when present.
        for key in ("agent", "tool", "node", "domain", "workflow", "run_id",
                    "session_id", "user", "status"):
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


_TEXT_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)-16s | "
    "trace=%(trace_id)s span=%(span_id)s req=%(request_id)s | %(message)s"
)


def configure_logging(service_name: str | None = None) -> None:
    """Configures the root logger with rotating-file + console sinks.

    Idempotent: safe to call from multiple entrypoints / processes.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    try:
        level = getattr(logging, Config.LOG_LEVEL, logging.INFO)
        root = logging.getLogger()
        root.setLevel(level)

        trace_filter = TraceContextFilter()
        formatter: logging.Formatter = (
            _JsonFormatter() if Config.LOG_JSON else logging.Formatter(_TEXT_FORMAT)
        )

        # Rotating file sink (durable).
        log_path = Config.LOG_FILE
        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=Config.LOG_MAX_BYTES,
            backupCount=Config.LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)  # capture everything to file
        file_handler.addFilter(trace_filter)
        file_handler.setFormatter(formatter)

        # Console sink (dev visibility, at configured level).
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.addFilter(trace_filter)
        console_handler.setFormatter(formatter)

        # Avoid duplicate handlers if reconfigured.
        root.handlers = [h for h in root.handlers
                         if not isinstance(h, (logging.handlers.RotatingFileHandler,))]
        root.addHandler(file_handler)
        # Only add a console handler if one isn't already present.
        if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
            root.addHandler(console_handler)

        # Tame noisy third-party loggers.
        for noisy in ("httpx", "httpcore", "uvicorn.access", "azure"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

        _CONFIGURED = True
        logging.getLogger(CAT_SYSTEM).info(
            "Logging configured (service=%s, level=%s, file=%s, json=%s)",
            service_name or Config.OTEL_SERVICE_NAME, Config.LOG_LEVEL,
            log_path, Config.LOG_JSON,
        )
    except Exception:
        # Last-resort: never let logging setup crash the app.
        logging.basicConfig(level=logging.INFO)


def get_logger(category: str) -> logging.Logger:
    """Returns a category logger (e.g. ``mesh.a2a``). Ensures config has run."""
    if not _CONFIGURED:
        configure_logging()
    return logging.getLogger(category)
