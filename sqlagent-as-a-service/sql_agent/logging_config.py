"""Central logging setup for the whole ``sql_agent`` tree.

Historically the only logger in the system was ``sql_agent.audit`` and nothing ever
configured it — Python's default level is WARNING with no handler, so every INFO
audit line was silently dropped and a request was impossible to trace.

This module installs a stdout handler, plus a rotating file handler (when
``settings.log_file`` is set), on the ``sql_agent`` parent logger. Every module logs
under ``sql_agent.<area>`` (service, agent, audit, query, db) so one config controls
them all. Call ``setup_logging()`` once at process start; it is idempotent, so
importing it from several entry points is safe.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from sql_agent.config import settings

# All loggers in the codebase live under this root so one handler catches everything.
ROOT_LOGGER_NAME = "sql_agent"

_CONFIGURED = False


def setup_logging(level: str | None = None) -> None:
    """Attach stdout + rotating-file handlers to the ``sql_agent`` logger tree.
    Idempotent."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    level_name = (level or settings.log_level or "INFO").upper()
    resolved = getattr(logging, level_name, logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)-18s | %(message)s",
        datefmt="%H:%M:%S",
    )

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    root = logging.getLogger(ROOT_LOGGER_NAME)
    root.setLevel(resolved)
    root.handlers.clear()
    root.addHandler(stream_handler)

    if settings.log_file:
        os.makedirs(os.path.dirname(settings.log_file) or ".", exist_ok=True)
        file_handler = RotatingFileHandler(
            settings.log_file,
            maxBytes=settings.log_file_max_bytes,
            backupCount=settings.log_file_backup_count,
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # Don't double-emit through the global root logger (e.g. uvicorn's handlers).
    root.propagate = False

    _CONFIGURED = True


def get_logger(area: str) -> logging.Logger:
    """Return a child logger ``sql_agent.<area>``. Ensures setup has run."""
    setup_logging()
    return logging.getLogger(f"{ROOT_LOGGER_NAME}.{area}")
