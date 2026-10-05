"""Integration tests for webhook persistence and reconciliation queueing.

Requires PostgreSQL and Redis (see CI service containers). Skipped when unavailable.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
import stripe
from app.core.config import get_settings
from app.core.database import Base, get_db
from app.core.redis import get_redis
from app.main import create_app
from app.models.merchant import Merchant
from app.models.probe_result import ProbeResultRecord
from app.models.reconciliation import ReconciliationJob, ReconciliationReportRecord
from app.models.webhook_event import WebhookEventRecord
from app.reconciliation.models import ReconciliationStatus
from app.reconciliation.processor import process_reconciliation_job
from app.reconciliation.queue import ReconciliationJobQueue
from app.webhooks.persistence import WebhookPersistenceService
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

VALID_PAYLOAD = json.dumps(
    {
        "id": "evt_integration_001",
        "type": "payment_intent.succeeded",
        "livemode": False,
        "api_version": "2024-06-20",
        "created": 1704067200,
        "data": {
            "object": {
                "id": "pi_integration_001",
                "object": "payment_intent",
                "amount": 5000,
                "currency": "usd",
                "status": "succeeded",
                "metadata": {},
            }
        },
    }
).encode()


def _make_stripe_event(merchant_id: uuid.UUID) -> MagicMock:
    obj = MagicMock()
    obj.id = "pi_integration_001"
    obj.status = "succeeded"
    obj.amount = 5000
    obj.currency = "usd"
    obj.metadata = {"merchant_id": str(merchant_id), "source": "snitchroot_probe"}

    event_data = MagicMock()
    event_data.object = obj

    event = MagicMock()
    event.id = "evt_integration_001"
    event.type = "payment_intent.succeeded"
    event.livemode = False
    event.api_version = "2024-06-20"
    event.created = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp())
    event.data = event_data
    return event


@pytest.fixture
def integration_services_available() -> bool:
    """Return True when PostgreSQL and Redis from test env are reachable."""
    import asyncio

    settings = get_settings()

    async def _probe() -> bool:
        try:
            engine = create_async_engine(str(settings.database_url), pool_pre_ping=True)
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            await engine.dispose()
            redis = Redis.from_url(str(settings.redis_url), decode_responses=True)
            await redis.ping()
            await redis.aclose()
            return True
        except Exception:
            return False

    return asyncio.run(_probe())


@pytest.fixture
async def integration_db(
    integration_services_available: bool,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    if not integration_services_available:
        pytest.skip("PostgreSQL/Redis not available for integration tests")

    settings = get_settings()
    engine = create_async_engine(str(settings.database_url), pool_pre_ping=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    yield session_factory
    await engine.dispose()


@pytest.fixture
async def integration_redis(integration_services_available: bool) -> AsyncGenerator[Redis, None]:
    if not integration_services_available:
        pytest.skip("PostgreSQL/Redis not available for integration tests")

    settings = get_settings()
    client = Redis.from_url(str(settings.redis_url), decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
async def integration_client(
    integration_db: async_sessionmaker[AsyncSession],
    integration_redis: Redis,
) -> AsyncGenerator[AsyncClient, None]:
    app = create_app()

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        async with integration_db() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_redis] = lambda: integration_redis

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture
async def seeded_merchant(integration_db: async_sessionmaker[AsyncSession]) -> Merchant:
    merchant = Merchant(id=uuid.uuid4(), name="Integration Test Merchant")
    async with integration_db() as session:
        session.add(merchant)
        await session.commit()
    return merchant


class TestWebhookPipelineIntegration:
    @pytest.mark.asyncio
    async def test_valid_webhook_persisted(
        self,
        integration_client: AsyncClient,
        integration_db: async_sessionmaker[AsyncSession],
        integration_redis: Redis,
        seeded_merchant: Merchant,
    ) -> None:
        mock_event = _make_stripe_event(seeded_merchant.id)
        with patch(
            "app.api.webhooks.stripe.Webhook.construct_event",
            return_value=mock_event,
        ):
            response = await integration_client.post(
                "/webhooks/stripe",
                content=VALID_PAYLOAD,
                headers={
                    "Content-Type": "application/json",
                    "stripe-signature": "t=1234,v1=valid",
                },
            )
        assert response.status_code == 200
        assert response.json() == {"status": "accepted"}

        async with integration_db() as session:
            count = await session.scalar(select(func.count()).select_from(WebhookEventRecord))
            assert count == 1
            row = await session.scalar(select(WebhookEventRecord))
            assert row is not None
            assert row.stripe_event_id == "evt_integration_001"
            assert row.amount == 5000
            assert row.currency == "usd"
            assert row.payment_intent_id == "pi_integration_001"
            assert row.merchant_id == seeded_merchant.id

        queue = ReconciliationJobQueue(integration_redis)
        assert await queue.depth() == 1

    @pytest.mark.asyncio
    async def test_duplicate_webhook_not_persisted_twice(
        self,
        integration_client: AsyncClient,
        integration_db: async_sessionmaker[AsyncSession],
        seeded_merchant: Merchant,
    ) -> None:
        mock_event = _make_stripe_event(seeded_merchant.id)
        with patch(
            "app.api.webhooks.stripe.Webhook.construct_event",
            return_value=mock_event,
        ):
            headers = {
                "Content-Type": "application/json",
                "stripe-signature": "t=1234,v1=valid",
            }
            first = await integration_client.post(
                "/webhooks/stripe", content=VALID_PAYLOAD, headers=headers
            )
            second = await integration_client.post(
                "/webhooks/stripe", content=VALID_PAYLOAD, headers=headers
            )
        assert first.json() == {"status": "accepted"}
        assert second.json() == {"status": "duplicate"}

        async with integration_db() as session:
            count = await session.scalar(select(func.count()).select_from(WebhookEventRecord))
            job_count = await session.scalar(select(func.count()).select_from(ReconciliationJob))
            assert count == 1
            assert job_count == 1

    @pytest.mark.asyncio
    async def test_raw_payload_not_persisted(
        self,
        integration_client: AsyncClient,
        integration_db: async_sessionmaker[AsyncSession],
        seeded_merchant: Merchant,
    ) -> None:
        mock_event = _make_stripe_event(seeded_merchant.id)
        with patch(
            "app.api.webhooks.stripe.Webhook.construct_event",
            return_value=mock_event,
        ):
            await integration_client.post(
                "/webhooks/stripe",
                content=VALID_PAYLOAD,
                headers={
                    "Content-Type": "application/json",
                    "stripe-signature": "t=1234,v1=valid",
                },
            )

        async with integration_db() as session:
            row = await session.scalar(select(WebhookEventRecord))
            assert row is not None
            columns = {c.name: getattr(row, c.name) for c in WebhookEventRecord.__table__.columns}
            serialized = json.dumps(columns, default=str)
            assert "payment_intent" not in serialized
            assert VALID_PAYLOAD.decode() not in serialized

    @pytest.mark.asyncio
    async def test_normalized_fields_survive_persistence(
        self,
        integration_client: AsyncClient,
        integration_db: async_sessionmaker[AsyncSession],
        seeded_merchant: Merchant,
    ) -> None:
        mock_event = _make_stripe_event(seeded_merchant.id)
        with patch(
            "app.api.webhooks.stripe.Webhook.construct_event",
            return_value=mock_event,
        ):
            await integration_client.post(
                "/webhooks/stripe",
                content=VALID_PAYLOAD,
                headers={
                    "Content-Type": "application/json",
                    "stripe-signature": "t=1234,v1=valid",
                },
            )

        async with integration_db() as session:
            row = await session.scalar(select(WebhookEventRecord))
            assert row is not None
            assert row.event_type == "payment_intent.succeeded"
            assert row.object_status == "succeeded"
            assert row.livemode is False
            assert row.api_version == "2024-06-20"
            assert row.metadata_json["merchant_id"] == str(seeded_merchant.id)

    @pytest.mark.asyncio
    async def test_reconciliation_job_created_once(
        self,
        integration_client: AsyncClient,
        integration_db: async_sessionmaker[AsyncSession],
        integration_redis: Redis,
        seeded_merchant: Merchant,
    ) -> None:
        mock_event = _make_stripe_event(seeded_merchant.id)
        with patch(
            "app.api.webhooks.stripe.Webhook.construct_event",
            return_value=mock_event,
        ):
            await integration_client.post(
                "/webhooks/stripe",
                content=VALID_PAYLOAD,
                headers={
                    "Content-Type": "application/json",
                    "stripe-signature": "t=1234,v1=valid",
                },
            )

        async with integration_db() as session:
            jobs = (await session.execute(select(ReconciliationJob))).scalars().all()
            assert len(jobs) == 1
            assert jobs[0].stripe_event_id == "evt_integration_001"
            assert jobs[0].status == "pending"

        assert await ReconciliationJobQueue(integration_redis).depth() == 1

    @pytest.mark.asyncio
    async def test_unknown_merchant_fails_safely(
        self,
        integration_client: AsyncClient,
        integration_db: async_sessionmaker[AsyncSession],
    ) -> None:
        unknown_merchant = uuid.uuid4()
        mock_event = _make_stripe_event(unknown_merchant)
        with patch(
            "app.api.webhooks.stripe.Webhook.construct_event",
            return_value=mock_event,
        ):
            response = await integration_client.post(
                "/webhooks/stripe",
                content=VALID_PAYLOAD,
                headers={
                    "Content-Type": "application/json",
                    "stripe-signature": "t=1234,v1=valid",
                },
            )
        assert response.status_code == 422
        assert "merchant" in response.json()["detail"].lower()

        async with integration_db() as session:
            assert await session.scalar(select(func.count()).select_from(WebhookEventRecord)) == 0

    @pytest.mark.asyncio
    async def test_invalid_signature_never_persists(
        self,
        integration_client: AsyncClient,
        integration_db: async_sessionmaker[AsyncSession],
    ) -> None:
        with patch(
            "app.api.webhooks.stripe.Webhook.construct_event",
            side_effect=stripe.SignatureVerificationError("Bad sig", "sig_header"),
        ):
            response = await integration_client.post(
                "/webhooks/stripe",
                content=VALID_PAYLOAD,
                headers={
                    "Content-Type": "application/json",
                    "stripe-signature": "t=bad,v1=bad",
                },
            )
        assert response.status_code == 400
        async with integration_db() as session:
            assert await session.scalar(select(func.count()).select_from(WebhookEventRecord)) == 0

    @pytest.mark.asyncio
    async def test_reconciliation_uses_engine(
        self,
        integration_db: async_sessionmaker[AsyncSession],
        integration_redis: Redis,
        seeded_merchant: Merchant,
    ) -> None:
        from app.reconciliation.engine import ReconciliationEngine
        from app.webhooks.models import NormalizedWebhookEvent, WebhookEventType

        probed_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        job_id: uuid.UUID
        async with integration_db() as session:
            session.add(
                ProbeResultRecord(
                    probe_id=uuid.uuid4(),
                    merchant_id=seeded_merchant.id,
                    status="succeeded",
                    stripe_intent_id="pi_integration_001",
                    amount=5000,
                    currency="usd",
                    idempotency_key="idem-001",
                    latency_ms=10,
                    probed_at=probed_at,
                )
            )
            normalized = NormalizedWebhookEvent(
                stripe_event_id="evt_integration_001",
                event_type=WebhookEventType.PAYMENT_INTENT_SUCCEEDED,
                livemode=False,
                api_version="2024-06-20",
                created_at=probed_at,
                object_id="pi_integration_001",
                object_status="succeeded",
                amount=5000,
                currency="usd",
                metadata={"merchant_id": str(seeded_merchant.id)},
            )
            persistence = WebhookPersistenceService(session, integration_redis)
            result = await persistence.persist_and_enqueue(normalized)
            job_id = result.reconciliation_job_id
            await session.commit()

        engine_instance = ReconciliationEngine()
        with patch.object(
            ReconciliationEngine,
            "reconcile",
            wraps=engine_instance.reconcile,
        ) as reconcile_spy:
            async with integration_db() as session:
                report = await process_reconciliation_job(session, job_id)
                await session.commit()
            reconcile_spy.assert_called_once()

        assert report.status == ReconciliationStatus.MATCHED
        async with integration_db() as session:
            stored = await session.scalar(select(ReconciliationReportRecord))
            assert stored is not None
            assert stored.status == "matched"

    @pytest.mark.asyncio
    async def test_duplicate_webhook_no_duplicate_reconciliation_work(
        self,
        integration_client: AsyncClient,
        integration_db: async_sessionmaker[AsyncSession],
        integration_redis: Redis,
        seeded_merchant: Merchant,
    ) -> None:
        mock_event = _make_stripe_event(seeded_merchant.id)
        with patch(
            "app.api.webhooks.stripe.Webhook.construct_event",
            return_value=mock_event,
        ):
            headers = {
                "Content-Type": "application/json",
                "stripe-signature": "t=1234,v1=valid",
            }
            await integration_client.post(
                "/webhooks/stripe",
                content=VALID_PAYLOAD,
                headers=headers,
            )
            await integration_client.post(
                "/webhooks/stripe",
                content=VALID_PAYLOAD,
                headers=headers,
            )

        async with integration_db() as session:
            job_count = await session.scalar(select(func.count()).select_from(ReconciliationJob))
            assert job_count == 1

        assert await ReconciliationJobQueue(integration_redis).depth() == 1
