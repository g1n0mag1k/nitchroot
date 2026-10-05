"""Unit tests for alerting channels (no database)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest
import respx
from app.alerting.channels import (
    PermanentAlertError,
    TransientAlertError,
    WebhookAlertChannel,
    build_channels,
)
from app.alerting.payload import IncidentAlertPayload
from app.core.config import get_settings
from app.persistence.enums import IncidentSeverity


@pytest.mark.asyncio
async def test_webhook_channel_success() -> None:
    url = "https://example.com/alerts"
    payload = IncidentAlertPayload(
        incident_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        discrepancy_type="discrepancy",
        severity=IncidentSeverity.HIGH,
        status="open",
        detected_at=datetime.now(tz=UTC),
        payment_intent_id="pi_123",
        reconciliation_job_id=uuid.uuid4(),
        dossier_filename="incident_x.pdf",
        dossier_sha256="a" * 64,
        manifest_filename="incident_x.manifest.json",
    )
    with respx.mock:
        route = respx.post(url).mock(return_value=httpx.Response(204))
        channel = WebhookAlertChannel(url, timeout_seconds=5.0)
        await channel.send(payload)
        assert route.called


@pytest.mark.asyncio
async def test_webhook_channel_5xx_is_transient() -> None:
    url = "https://example.com/alerts"
    payload = IncidentAlertPayload(
        incident_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        discrepancy_type="discrepancy",
        severity=IncidentSeverity.HIGH,
        status="open",
        detected_at=datetime.now(tz=UTC),
        payment_intent_id=None,
        reconciliation_job_id=uuid.uuid4(),
        dossier_filename=None,
        dossier_sha256=None,
        manifest_filename=None,
    )
    with respx.mock:
        respx.post(url).mock(return_value=httpx.Response(503))
        channel = WebhookAlertChannel(url, timeout_seconds=5.0)
        with pytest.raises(TransientAlertError):
            await channel.send(payload)


@pytest.mark.asyncio
async def test_webhook_channel_4xx_is_permanent() -> None:
    url = "https://example.com/alerts"
    payload = IncidentAlertPayload(
        incident_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        discrepancy_type="discrepancy",
        severity=IncidentSeverity.HIGH,
        status="open",
        detected_at=datetime.now(tz=UTC),
        payment_intent_id=None,
        reconciliation_job_id=uuid.uuid4(),
        dossier_filename=None,
        dossier_sha256=None,
        manifest_filename=None,
    )
    with respx.mock:
        respx.post(url).mock(return_value=httpx.Response(401))
        channel = WebhookAlertChannel(url, timeout_seconds=5.0)
        with pytest.raises(PermanentAlertError):
            await channel.send(payload)


def test_build_channels_empty_without_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    get_settings.cache_clear()
    settings = get_settings()
    assert build_channels(settings) == []
