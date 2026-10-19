"""stdlib ``logging`` → OTel logs bridge built on the OTel Logs API.

Replaces the SDK's deprecated ``LoggingHandler`` (whose successor lives in an
``opentelemetry-instrumentation-*`` package, which principle 1 forbids). Log
bodies are PII-scrubbed before emission; the active span context is attached so
every record correlates with its trace.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from opentelemetry import context as otel_context

from gernas.utils.sanitizer import scrub_pii

_SEVERITY = {logging.DEBUG: 5, logging.INFO: 9, logging.WARNING: 13, logging.ERROR: 17, logging.CRITICAL: 21}

# OTel's own diagnostics are never exported through OTel (a feedback loop if a configured
# log namespace covers them).
_SELF_LOGGERS = ("opentelemetry", "grpc")
# The stdlib calls flush() while holding the global logging lock (dictConfig, e.g. uvicorn
# startup; logging.shutdown at exit). The OTel force_flush ignores its timeout and waits for
# the in-flight export, whose retry warnings need that same lock when the Collector is down,
# so the flush runs on a helper thread and the caller waits at most this long.
_FLUSH_WAIT_S = 1.0


def _severity(levelno: int) -> Any:
    from opentelemetry._logs import SeverityNumber

    for level in (logging.CRITICAL, logging.ERROR, logging.WARNING, logging.INFO):
        if levelno >= level:
            return SeverityNumber(_SEVERITY[level])
    return SeverityNumber(_SEVERITY[logging.DEBUG])


class GernasLogHandler(logging.Handler):
    """Forward stdlib log records to an OTel ``LoggerProvider``."""

    def __init__(self, logger_provider: Any, level: int = logging.INFO) -> None:
        super().__init__(level)
        self._provider = logger_provider
        self._loggers: dict[str, Any] = {}

    def _logger(self, name: str) -> Any:
        logger = self._loggers.get(name)
        if logger is None:
            logger = self._provider.get_logger(name)
            self._loggers[name] = logger
        return logger

    def filter(self, record: logging.LogRecord) -> bool:
        # Runs before logging.Handler.handle() takes the handler lock.
        if record.name.startswith(_SELF_LOGGERS):
            return False
        return bool(super().filter(record))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            body = scrub_pii(record.getMessage())
            attrs: dict[str, Any] = {
                "code.function.name": record.funcName,
                "code.line.number": record.lineno,
                "log.logger.name": record.name,
            }
            if record.exc_info and record.exc_info[1] is not None:
                attrs["exception.type"] = type(record.exc_info[1]).__name__
                attrs["exception.message"] = scrub_pii(str(record.exc_info[1]))
            self._logger(record.name).emit(
                timestamp=int(record.created * 1e9),
                observed_timestamp=time.time_ns(),
                context=otel_context.get_current(),
                severity_number=_severity(record.levelno),
                severity_text=record.levelname,
                body=body,
                attributes=attrs,
            )
        except Exception:  # noqa: BLE001 - logging must never raise into the application
            pass

    def flush(self) -> None:
        try:
            worker = threading.Thread(target=self._force_flush, name="GernasLogFlush", daemon=True)
            worker.start()
            worker.join(_FLUSH_WAIT_S)
        except Exception:  # noqa: BLE001
            pass

    def _force_flush(self) -> None:
        try:
            self._provider.force_flush()
        except Exception:  # noqa: BLE001
            pass
