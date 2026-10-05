"""Unit tests for merchant resolution."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.merchants.resolver import MerchantResolutionError, resolve_merchant_for_webhook
from app.models.merchant import Merchant
from app.webhooks.models import NormalizedWebhookEvent, WebhookEventType


@pytest.mark.asyncio
async def test_resolve_merchant_success() -> None:
    merchant = Merchant(id=uuid.uuid4(), name="Acme")
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = merchant
    session.execute = AsyncMock(return_value=result)

    event = NormalizedWebhookEvent(
        stripe_event_id="evt_1",
        event_type=WebhookEventType.PAYMENT_INTENT_SUCCEEDED,
        metadata={"merchant_id": str(merchant.id)},
    )
    resolved = await resolve_merchant_for_webhook(session, event)
    assert resolved.id == merchant.id


@pytest.mark.asyncio
async def test_resolve_merchant_unknown_raises() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result)

    event = NormalizedWebhookEvent(
        stripe_event_id="evt_1",
        metadata={"merchant_id": str(uuid.uuid4())},
    )
    with pytest.raises(MerchantResolutionError):
        await resolve_merchant_for_webhook(session, event)


@pytest.mark.asyncio
async def test_resolve_merchant_missing_metadata_raises() -> None:
    session = AsyncMock()
    event = NormalizedWebhookEvent(stripe_event_id="evt_1")
    with pytest.raises(MerchantResolutionError):
        await resolve_merchant_for_webhook(session, event)
