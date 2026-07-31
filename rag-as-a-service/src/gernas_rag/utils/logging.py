"""Structured logging setup (structlog)."""

import logging
import logging.handlers
from pathlib import Path

import structlog

_LOG_DIR = Path("logs")
_LOG_FILE = _LOG_DIR / "app.log"

# These libraries log through stdlib logging directly (not structlog), and since
# our handlers sit on the root logger, their INFO/DEBUG chatter (raw HTTP wire
# tracing, model-download HEAD requests, the --reload file watcher noticing its
# own log writes, ...) would otherwise drown out gernas_rag's own events. Real
# problems from these libraries still surface — WARNING and above still pass.
_NOISY_THIRD_PARTY_LOGGERS = [
    "httpx",
    "httpcore",
    "urllib3",
    "huggingface_hub",
    "filelock",
    "watchfiles",
    "asyncio",
    "transformers",
    "sentence_transformers",
    "FlagEmbedding",
    "PIL",
]


def configure_logging(level: str = "INFO") -> None:
    # Shared event-processing chain (timestamps, levels, exception formatting).
    # Runs once per log call; the stdlib handlers below only render the result.
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=shared_processors
        + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Route through stdlib logging so both a console stream and a log file can
    # render the same structured event — structlog's own PrintLoggerFactory only
    # supports a single output.
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),  # JSON logs for production
        ],
        foreign_pre_chain=shared_processors,
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        _LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [console_handler, file_handler]
    root_logger.setLevel(level)

    for name in _NOISY_THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.BoundLogger:
    # Bind the module name explicitly. ``add_logger_name`` is not used because the
    # PrintLogger factory produces loggers without a ``.name`` attribute.
    return structlog.get_logger().bind(logger=name)
