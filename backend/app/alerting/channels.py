"""Alert channel implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import httpx
import structlog

from app.alerting.payload import IncidentAlertPayload
from app.core.config import Settings

log = structlog.get_logger(__name__)


class AlertChannel(ABC):
    """Deliver an incident alert to an external system."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Channel identifier stored in alert_deliveries."""

    @abstractmethod
    async def send(self, payload: IncidentAlertPayload) -> None:
        """Send the alert. Raises on transient or permanent failure."""


class WebhookAlertChannel(AlertChannel):
    """Generic HTTPS webhook alert channel."""

    def __init__(self, url: str, *, timeout_seconds: float) -> None:
        self._url = url
        self._timeout = timeout_seconds

    @property
    def name(self) -> str:
        return "webhook"

    async def send(self, payload: IncidentAlertPayload) -> None:
        body: dict[str, Any] = payload.to_dict()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self._url, json=body)
        if response.status_code >= 500:
            raise TransientAlertError(f"webhook returned {response.status_code}")
        if response.status_code >= 400:
            raise PermanentAlertError(f"webhook returned {response.status_code}")
        log.info(
            "alert_webhook_sent",
            incident_id=str(payload.incident_id),
            status_code=response.status_code,
        )


class TransientAlertError(Exception):
    """Retryable delivery failure (network, 5xx)."""


class PermanentAlertError(Exception):
    """Non-retryable configuration or client error (4xx)."""


def build_channels(settings: Settings) -> list[AlertChannel]:
    channels: list[AlertChannel] = []
    if settings.alert_webhook_url:
        channels.append(
            WebhookAlertChannel(
                settings.alert_webhook_url,
                timeout_seconds=settings.alert_http_timeout_seconds,
            )
        )
    return channels
