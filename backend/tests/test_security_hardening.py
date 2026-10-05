"""Tests for security headers, rate limits, and log redaction."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from app.core.logging import _redact_secrets_processor
from app.core.rate_limit import rate_limit_exceeded_handler
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_security_headers_on_health(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.headers["strict-transport-security"].startswith("max-age=")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["content-security-policy"] == "default-src 'none'"
    assert response.headers["referrer-policy"] == "no-referrer"


@pytest.mark.asyncio
async def test_register_merchant_returns_201(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/merchants",
        json={
            "name": "Acme Corp",
            "stripe_restricted_key": "rk_test_placeholder",
            "probe_interval_seconds": 900,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Acme Corp"
    assert "id" in body


@pytest.mark.asyncio
async def test_merchant_rate_limit_returns_429_with_retry_after(
    client: AsyncClient,
) -> None:
    payload = {
        "name": "Rate Limited Shop",
        "stripe_restricted_key": "rk_test_placeholder",
    }
    for _ in range(10):
        response = await client.post("/api/v1/merchants", json=payload)
        assert response.status_code == 201

    response = await client.post("/api/v1/merchants", json=payload)
    assert response.status_code == 429
    assert "retry-after" in response.headers


@pytest.mark.asyncio
async def test_rate_limit_handler_sets_retry_after() -> None:
    from starlette.requests import Request

    request = Request({"type": "http", "headers": [], "method": "POST", "path": "/"})
    exc = MagicMock()
    exc.detail = "10 per 1 hour"
    exc.retry_after = 120
    response = await rate_limit_exceeded_handler(request, exc)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "120"


def test_log_redaction_processor_scrubs_secrets() -> None:
    event = {
        "stripe_key": "rk_live_abc123",
        "webhook": "whsec_testsecret",
        "auth": "Bearer abcDEF123",
        "nested": {"token": "sk_live_should_redact"},
    }
    cleaned = _redact_secrets_processor(None, "info", event)
    assert cleaned["stripe_key"] == "<REDACTED>"
    assert cleaned["webhook"] == "<REDACTED>"
    assert cleaned["auth"] == "<REDACTED>"
    assert cleaned["nested"]["token"] == "<REDACTED>"


def test_stripe_webhook_route_is_rate_limited() -> None:
    from app.api.webhooks import ingest_stripe_webhook

    assert hasattr(ingest_stripe_webhook, "__wrapped__"), (
        "stripe webhook should use slowapi limit decorator"
    )
