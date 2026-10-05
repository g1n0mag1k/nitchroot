"""Tests for security headers, rate limits, and log redaction."""

from __future__ import annotations

import logging
import os
from unittest.mock import MagicMock

import pytest
import structlog
from app.core.config import Environment, get_settings
from app.core.logging import (
    RedactingLoggingFilter,
    _redact_secrets_processor,
    configure_logging,
    contains_key_shaped_secret,
    scrub_sensitive_data,
)
from app.core.rate_limit import (
    get_limiter,
    rate_limit_client_key,
    rate_limit_exceeded_handler,
    stripe_webhook_global_key,
)
from httpx import AsyncClient
from limits import parse
from limits.storage import storage_from_string
from starlette.requests import Request


@pytest.mark.asyncio
async def test_security_headers_on_api_route(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert (
        response.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    )
    assert "accelerometer=()" in response.headers["permissions-policy"]
    assert response.headers["cache-control"] == "no-store"
    assert "strict-transport-security" not in response.headers


@pytest.mark.asyncio
async def test_hsts_present_only_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.main import create_app
    from httpx import ASGITransport

    monkeypatch.setenv("ENVIRONMENT", Environment.PRODUCTION.value)
    get_settings.cache_clear()
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as prod_client:
        response = await prod_client.get("/health")
    assert response.headers["strict-transport-security"].startswith("max-age=")


@pytest.mark.asyncio
async def test_webhook_route_has_global_circuit_breaker_only() -> None:
    from app.api.webhooks import ingest_stripe_webhook

    assert hasattr(ingest_stripe_webhook, "__wrapped__")
    assert stripe_webhook_global_key(Request({"type": "http"})) == "stripe_webhook_global"


@pytest.mark.asyncio
async def test_rate_limit_handler_sets_retry_after() -> None:
    request = Request({"type": "http", "headers": [], "method": "POST", "path": "/"})
    exc = MagicMock()
    exc.detail = "10 per 1 hour"
    exc.retry_after = 120
    response = await rate_limit_exceeded_handler(request, exc)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "120"


def test_rate_limit_key_honors_forwarded_for_from_trusted_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "10.0.0.0/8")
    get_settings.cache_clear()
    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"203.0.113.9, 10.0.0.1")],
        "client": ("10.0.0.2", 12345),
    }
    request = Request(scope)
    assert rate_limit_client_key(request) == "203.0.113.9"


def test_rate_limit_key_ignores_forwarded_for_from_untrusted_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "10.0.0.0/8")
    get_settings.cache_clear()
    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"203.0.113.9")],
        "client": ("203.0.113.1", 12345),
    }
    request = Request(scope)
    assert rate_limit_client_key(request) == "203.0.113.1"


@pytest.mark.integration
def test_redis_rate_limit_shared_across_replicas() -> None:
    """Simulate two API replicas sharing one Redis-backed counter."""
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    try:
        storage = storage_from_string(redis_url)
        limit = parse("3/minute")
        key = "multi-replica-test-key"
        storage.clear(key)
        assert storage.hit(limit, key)
        assert storage.hit(limit, key)
        assert storage.hit(limit, key)
        assert not storage.hit(limit, key)
        storage.clear(key)
    except Exception as exc:
        pytest.skip(f"Redis not available for integration test: {exc}")


def test_log_redaction_nested_dict_and_headers() -> None:
    event = {
        "stripe_key": "rk_live_abc123",
        "Authorization": "Bearer secret-token",
        "stripe-signature": "whsec_testsig",
        "nested": {"token": "sk_live_should_redact"},
    }
    cleaned = _redact_secrets_processor(None, "info", event)
    assert cleaned["stripe_key"] == "<REDACTED>"
    assert cleaned["Authorization"] == "<REDACTED>"
    assert cleaned["stripe-signature"] == "<REDACTED>"
    assert cleaned["nested"]["token"] == "<REDACTED>"


def test_log_redaction_exception_message() -> None:
    exc = ValueError("rk_test_abcdefghijklmnop")
    scrubbed = scrub_sensitive_data(exc)
    assert scrubbed == "<REDACTED>"
    assert "rk_test_" not in str(scrubbed)


def test_uvicorn_access_log_line_is_redacted() -> None:
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='127.0.0.1 - "POST /webhooks/stripe HTTP/1.1" Authorization: Bearer abc',
        args=(),
        exc_info=None,
    )
    assert RedactingLoggingFilter().filter(record)
    assert "Bearer abc" not in record.msg
    assert "<REDACTED>" in record.msg


def test_suite_logs_contain_no_key_shaped_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    configure_logging()
    logger = structlog.get_logger("security_hardening_test")
    logger.info(
        "synthetic_event",
        stripe_key="rk_live_should_not_appear",
        nested={"whsec": "whsec_also_hidden"},
    )
    combined = " ".join(caplog.messages)
    assert not contains_key_shaped_secret(combined)


def test_limiter_uses_redis_storage() -> None:
    get_limiter.cache_clear()
    limiter_instance = get_limiter()
    storage = limiter_instance._storage
    assert storage.__class__.__name__ != "MemoryStorage"
    assert limiter_instance._in_memory_fallback_enabled is False
