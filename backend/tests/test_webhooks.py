"""Tests for webhook ingestion, normalization, and deduplication.

All Stripe and Redis calls are mocked. No live network calls.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import stripe
from httpx import AsyncClient

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_stripe_event(
    event_id: str = "evt_test_001",
    event_type: str = "payment_intent.succeeded",
    livemode: bool = False,
    amount: int = 5000,
    currency: str = "usd",
    object_status: str = "succeeded",
    metadata: dict | None = None,
    api_version: str | None = "2024-06-20",
) -> MagicMock:
    """Build a minimal mock stripe.Event."""
    obj = MagicMock()
    obj.id = "pi_test_001"
    obj.status = object_status
    obj.amount = amount
    obj.currency = currency
    obj.metadata = metadata or {"merchant_id": "merch_123", "source": "snitchroot_probe"}

    event_data = MagicMock()
    event_data.object = obj

    event = MagicMock()
    event.id = event_id
    event.type = event_type
    event.livemode = livemode
    event.api_version = api_version
    event.created = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp())
    event.data = event_data
    return event


# ── normalizer unit tests ─────────────────────────────────────────────────────


class TestNormalizer:
    def test_normalize_payment_intent_succeeded(self) -> None:
        from app.webhooks.models import WebhookEventType
        from app.webhooks.normalizer import normalize_stripe_event

        event = _make_stripe_event()
        result = normalize_stripe_event(event)

        assert result.stripe_event_id == "evt_test_001"
        assert result.event_type == WebhookEventType.PAYMENT_INTENT_SUCCEEDED
        assert result.amount == 5000
        assert result.currency == "usd"
        assert result.object_id == "pi_test_001"
        assert result.livemode is False
        assert "merchant_id" in result.metadata

    def test_normalize_unknown_event_type(self) -> None:
        from app.webhooks.models import WebhookEventType
        from app.webhooks.normalizer import normalize_stripe_event

        event = _make_stripe_event(event_type="some.future.event")
        result = normalize_stripe_event(event)
        assert result.event_type == WebhookEventType.UNKNOWN

    def test_pii_metadata_keys_stripped(self) -> None:
        from app.webhooks.normalizer import normalize_stripe_event

        event = _make_stripe_event(
            metadata={
                "merchant_id": "merch_123",
                "customer_email": "user@example.com",  # PII — must be stripped
                "customer_name": "Jane Doe",  # PII — must be stripped
                "safe_key": "safe_value",
            }
        )
        result = normalize_stripe_event(event)
        assert "customer_email" not in result.metadata
        assert "customer_name" not in result.metadata
        assert result.metadata.get("merchant_id") == "merch_123"
        assert result.metadata.get("safe_key") == "safe_value"

    def test_payout_event_normalized(self) -> None:
        from app.webhooks.models import WebhookEventType
        from app.webhooks.normalizer import normalize_stripe_event

        event = _make_stripe_event(
            event_id="evt_payout_001",
            event_type="payout.paid",
            amount=100_000,
            currency="usd",
            object_status="paid",
        )
        result = normalize_stripe_event(event)
        assert result.event_type == WebhookEventType.PAYOUT_PAID
        assert result.amount == 100_000

    def test_normalize_handles_missing_optional_fields(self) -> None:
        from app.webhooks.normalizer import normalize_stripe_event

        event = _make_stripe_event(amount=0, currency="", object_status="", metadata={})
        event.api_version = None
        # Force falsy getattr fallbacks
        event.data.object.amount = None
        event.data.object.currency = None
        event.data.object.status = None
        event.data.object.metadata = None

        result = normalize_stripe_event(event)
        assert result.amount == 0
        assert result.currency == ""
        assert result.object_status == ""
        assert result.api_version == ""
        assert result.metadata == {}


# ── deduplicator unit tests ────────────────────────────────────────────────────


class TestDeduplicator:
    @pytest.mark.asyncio
    async def test_first_delivery_not_duplicate(self) -> None:
        from app.webhooks.deduplicator import WebhookDeduplicator

        mock_redis = AsyncMock()
        mock_redis.set.return_value = True  # SET NX succeeded → new event
        dedup = WebhookDeduplicator(mock_redis)
        assert await dedup.is_duplicate("evt_001") is False
        mock_redis.set.assert_awaited_once_with(
            "snitchroot:webhook:seen:evt_001", "1", nx=True, ex=86_400
        )

    @pytest.mark.asyncio
    async def test_second_delivery_is_duplicate(self) -> None:
        from app.webhooks.deduplicator import WebhookDeduplicator

        mock_redis = AsyncMock()
        mock_redis.set.return_value = None  # SET NX failed → already exists
        dedup = WebhookDeduplicator(mock_redis)
        assert await dedup.is_duplicate("evt_001") is True


# ── redis factory unit tests ───────────────────────────────────────────────────


class TestRedisFactory:
    @pytest.mark.asyncio
    async def test_get_redis_returns_client_from_pool(self) -> None:
        from app.core import redis as redis_module

        redis_module._get_redis_pool.cache_clear()
        mock_pool = MagicMock()
        with (
            patch.object(
                redis_module.aioredis.ConnectionPool,
                "from_url",
                return_value=mock_pool,
            ) as mock_from_url,
            patch.object(redis_module.aioredis, "Redis") as mock_redis_cls,
        ):
            mock_client = MagicMock()
            mock_redis_cls.return_value = mock_client
            client = await redis_module.get_redis()

        assert client is mock_client
        mock_from_url.assert_called_once()
        mock_redis_cls.assert_called_once_with(connection_pool=mock_pool, decode_responses=True)
        redis_module._get_redis_pool.cache_clear()


# ── webhook API endpoint tests ─────────────────────────────────────────────────


VALID_PAYLOAD = json.dumps(
    {
        "id": "evt_test_001",
        "type": "payment_intent.succeeded",
        "livemode": False,
        "api_version": "2024-06-20",
        "created": 1704067200,
        "data": {
            "object": {
                "id": "pi_test_001",
                "object": "payment_intent",
                "amount": 5000,
                "currency": "usd",
                "status": "succeeded",
                "metadata": {"merchant_id": "merch_123"},
            }
        },
    }
).encode()


class TestWebhookEndpoint:
    @pytest.mark.asyncio
    async def test_missing_signature_returns_400(self, client: AsyncClient) -> None:
        response = await client.post(
            "/webhooks/stripe",
            content=VALID_PAYLOAD,
            headers={"Content-Type": "application/json"},
            # No stripe-signature header
        )
        assert response.status_code == 400
        assert "Missing Stripe-Signature" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_invalid_signature_returns_400(self, client: AsyncClient) -> None:
        with patch(
            "app.api.webhooks.stripe.Webhook.construct_event",
            side_effect=stripe.SignatureVerificationError("Bad sig", "sig_header"),
        ):
            response = await client.post(
                "/webhooks/stripe",
                content=VALID_PAYLOAD,
                headers={
                    "Content-Type": "application/json",
                    "stripe-signature": "t=bad,v1=bad",
                },
            )
        assert response.status_code == 400
        assert "Invalid Stripe signature" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_valid_event_accepted(self, client: AsyncClient, mock_redis: AsyncMock) -> None:
        mock_event = _make_stripe_event()
        mock_redis.set.return_value = True  # not duplicate
        with patch(
            "app.api.webhooks.stripe.Webhook.construct_event",
            return_value=mock_event,
        ) as mock_construct:
            response = await client.post(
                "/webhooks/stripe",
                content=VALID_PAYLOAD,
                headers={
                    "Content-Type": "application/json",
                    "stripe-signature": "t=1234,v1=valid",
                },
            )
        assert response.status_code == 200
        assert response.json() == {"status": "accepted"}
        mock_construct.assert_called_once()
        call_kwargs = mock_construct.call_args.kwargs
        assert call_kwargs["payload"] == VALID_PAYLOAD
        assert call_kwargs["sig_header"] == "t=1234,v1=valid"
        assert call_kwargs["secret"] == "whsec_placeholder_do_not_use"

    @pytest.mark.asyncio
    async def test_duplicate_event_returns_200_duplicate(
        self, client: AsyncClient, mock_redis: AsyncMock
    ) -> None:
        mock_event = _make_stripe_event()
        mock_redis.set.return_value = None  # duplicate
        with patch(
            "app.api.webhooks.stripe.Webhook.construct_event",
            return_value=mock_event,
        ):
            response = await client.post(
                "/webhooks/stripe",
                content=VALID_PAYLOAD,
                headers={
                    "Content-Type": "application/json",
                    "stripe-signature": "t=1234,v1=valid",
                },
            )
        assert response.status_code == 200
        assert response.json() == {"status": "duplicate"}

    @pytest.mark.asyncio
    async def test_construct_event_generic_error_returns_400(self, client: AsyncClient) -> None:
        with patch(
            "app.api.webhooks.stripe.Webhook.construct_event",
            side_effect=ValueError("Malformed payload"),
        ):
            response = await client.post(
                "/webhooks/stripe",
                content=b"not json",
                headers={
                    "Content-Type": "application/json",
                    "stripe-signature": "t=1234,v1=valid",
                },
            )
        assert response.status_code == 400
        assert "Webhook payload error" in response.json()["detail"]
