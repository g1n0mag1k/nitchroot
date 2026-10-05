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
import re
import sys
import traceback
from typing import cast

import structlog
from structlog.types import EventDict, WrappedLogger

from app.core.config import Environment, get_settings

_REDACTED = "<REDACTED>"
_SENSITIVE_HEADER_KEYS = frozenset(
    {"authorization", "stripe-signature", "http_authorization"},
)
_SENSITIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"rk_live_\S+"),
    re.compile(r"rk_test_\S+"),
    re.compile(r"sk_live_\S+"),
    re.compile(r"sk_test_\S+"),
    re.compile(r"whsec_\S+"),
    re.compile(r"Bearer\s+\S+", re.IGNORECASE),
)
_KEY_SHAPED_PATTERN = re.compile(
    r"(rk_live_|rk_test_|sk_live_|sk_test_|whsec_)[A-Za-z0-9]+",
)


def scrub_secrets_text(value: str) -> str:
    """Replace sensitive substrings in a single string value."""
    scrubbed = value
    for pattern in _SENSITIVE_PATTERNS:
        scrubbed = pattern.sub(_REDACTED, scrubbed)
    return scrubbed


def _scrub_mapping_key(key: object) -> object:
    if isinstance(key, str) and key.lower() in _SENSITIVE_HEADER_KEYS:
        return _REDACTED
    return key


def scrub_sensitive_data(value: object) -> object:
    """Recursively redact sensitive material from log event values."""
    if isinstance(value, BaseException):
        return scrub_secrets_text(str(value))
    if isinstance(value, str):
        return scrub_secrets_text(value)
    if isinstance(value, dict):
        return {_scrub_mapping_key(key): scrub_sensitive_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_sensitive_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub_sensitive_data(item) for item in value)
    return value


def _redact_secrets_processor(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Structlog processor that scrubs secrets from all event fields."""
    for key, value in list(event_dict.items()):
        if isinstance(key, str) and key.lower() in _SENSITIVE_HEADER_KEYS:
            event_dict[key] = _REDACTED
            continue
        event_dict[key] = scrub_sensitive_data(value)
    if "exception" in event_dict:
        event_dict["exception"] = scrub_secrets_text(str(event_dict["exception"]))
    return event_dict


class RedactingLoggingFilter(logging.Filter):
    """Scrub secrets from stdlib log records (uvicorn access lines, SQLAlchemy, etc.)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = scrub_secrets_text(record.msg)
        if record.args:
            record.args = tuple(scrub_sensitive_data(arg) for arg in record.args)
        if record.exc_info:
            record.exc_text = scrub_secrets_text(
                "".join(traceback.format_exception(*record.exc_info)),
            )
        return True


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
        _redact_secrets_processor,
        _drop_color_message_key,
    ]

    if is_development:
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer(colors=True)
    else:
        renderer = structlog.processors.JSONRenderer()

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(RedactingLoggingFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.getLevelName(settings.log_level.value))

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.log_level.value),
        ),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    for name in ["uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy.engine"]:
        named = logging.getLogger(name)
        named.handlers = []
        named.propagate = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    Return a structlog logger bound to the given module name.

    Args:
        name: Typically __name__ of the calling module.

    Returns:
        A structlog BoundLogger instance.
    """
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))


def contains_key_shaped_secret(text: str) -> bool:
    """Return True if text still contains a Stripe-like secret token."""
    return _KEY_SHAPED_PATTERN.search(text) is not None
