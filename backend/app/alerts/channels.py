"""Outbound alert channel implementations."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from typing import Protocol

import httpx

from app.core.logging import get_logger
from app.models.merchant import Merchant
from app.models.recon_discrepancy import ReconDiscrepancy

logger = get_logger(__name__)

_HTTP_TIMEOUT_SECONDS = 10.0
_SIGNATURE_HEADER = "X-SnitchRoot-Signature"


class AlertChannel(Protocol):
    """Delivers a reconciliation discrepancy alert to an operator channel."""

    async def send(self, discrepancy: ReconDiscrepancy, merchant: Merchant) -> bool:
        """Send the alert. Returns True when the remote endpoint accepts (2xx)."""
        ...


def _severity_emoji(severity: str) -> str:
    normalized = severity.upper()
    if normalized == "HIGH":
        return "🔴"
    if normalized == "MEDIUM":
        return "🟡"
    return "⚪"


def _format_detected_at(detected_at: datetime) -> str:
    if detected_at.tzinfo is None:
        return detected_at.isoformat()
    return detected_at.isoformat()


class SlackChannel:
    """Posts alerts to a Slack incoming webhook."""

    def __init__(self, webhook_url: str) -> None:
        self._webhook_url = webhook_url

    async def send(self, discrepancy: ReconDiscrepancy, merchant: Merchant) -> bool:
        emoji = _severity_emoji(discrepancy.severity)
        text = (
            f"{emoji} {discrepancy.severity} — {merchant.name}\n"
            f"{discrepancy.description}\n"
            f"Detected at: {_format_detected_at(discrepancy.detected_at)}"
        )
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    self._webhook_url,
                    json={"text": text},
                )
        except httpx.HTTPError as exc:
            logger.warning(
                "slack_alert_delivery_failed",
                merchant_id=str(merchant.id),
                discrepancy_id=str(discrepancy.id),
                error=str(exc),
            )
            return False

        if response.is_success:
            return True

        logger.warning(
            "slack_alert_rejected",
            merchant_id=str(merchant.id),
            discrepancy_id=str(discrepancy.id),
            status_code=response.status_code,
        )
        return False


class GenericWebhookChannel:
    """POSTs signed JSON to a merchant-operated alert webhook."""

    def __init__(self, url: str, secret: str) -> None:
        self._url = url
        self._secret = secret

    async def send(self, discrepancy: ReconDiscrepancy, merchant: Merchant) -> bool:
        payload = {
            "discrepancy_id": str(discrepancy.id),
            "merchant_id": str(merchant.id),
            "merchant_name": merchant.name,
            "severity": discrepancy.severity,
            "description": discrepancy.description,
            "detected_at": _format_detected_at(discrepancy.detected_at),
            "stripe_event_id": discrepancy.stripe_event_id,
        }
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        signature = hmac.new(
            self._secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        headers = {
            "Content-Type": "application/json",
            _SIGNATURE_HEADER: signature,
        }

        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
                response = await client.post(self._url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning(
                "webhook_alert_delivery_failed",
                merchant_id=str(merchant.id),
                discrepancy_id=str(discrepancy.id),
                error=str(exc),
            )
            return False

        if response.is_success:
            return True

        logger.warning(
            "webhook_alert_rejected",
            merchant_id=str(merchant.id),
            discrepancy_id=str(discrepancy.id),
            status_code=response.status_code,
        )
        return False


def verify_webhook_signature(body: bytes, secret: str, signature: str) -> bool:
    """Verify an X-SnitchRoot-Signature HMAC-SHA256 hex digest."""
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
