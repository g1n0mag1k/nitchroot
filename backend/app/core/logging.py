"""
Structured JSON logging configuration via structlog.

Emits machine-readable JSON in production/test environments and
pretty-printed console output in development. Every log record
includes: timestamp, level, logger name, environment, and any
bound context key-value pairs.

Usage:
    from app.core.logging import get_logger

    logger = get_logger(__name__)
    logger.info("probe_succeeded", merchant_id=str(merchant_id), latency_ms=42)
"""

import logging
import sys

import structlog
from structlog.types import EventDict, WrappedLogger

from app.core.config import Environment, get_settings


def _drop_color_message_key(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """
    Remove the 'color_message' key injected by uvicorn's access logger.

    Keeps JSON output clean when running behind uvicorn.
    """
    event_dict.pop("color_message", None)
    return event_dict


def configure_logging() -> None:
    """
    Configure structlog and the stdlib logging bridge.

    Call once at application startup before any log statements.
    """
    settings = get_settings()
    is_development = settings.environment == Environment.DEVELOPMENT

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        _drop_color_message_key,
    ]

    if is_development:
        # Human-readable output for local development
        processors: list[structlog.types.Processor] = [
            *shared_processors,
            structlog.dev.ConsoleRenderer(colors=True),
        ]
    else:
        # Machine-readable JSON for production / CI
        processors = [
            *shared_processors,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.log_level.value)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Bridge stdlib logging into structlog so uvicorn, SQLAlchemy, etc.
    # all emit structured records.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.getLevelName(settings.log_level.value),
    )

    for name in ["uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy.engine"]:
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    Return a structlog logger bound to the given module name.

    Args:
        name: Typically __name__ of the calling module.

    Returns:
        A structlog BoundLogger instance.
    """
    return structlog.get_logger(name)
