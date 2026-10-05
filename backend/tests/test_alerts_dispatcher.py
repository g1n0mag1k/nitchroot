"""Tests for alert channel delivery and dispatcher retry behavior."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import httpx
import pytest
import respx
from app.alerts.channels import verify_webhook_signature
from app.alerts.dispatcher import CHANNEL_SLACK, AlertDispatcher
from app.core.config import get_settings
from app.models.alert_delivery import AlertDelivery
from app.models.merchant import Merchant
from app.models.recon_discrepancy import ReconDiscrepancy
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_SLACK_URL = "https://hooks.slack.com/services/T00/B00/XX"
_WEBHOOK_URL = "https://alerts.example.com/snitchroot"
_SIGNING_SECRET = "a" * 64


@pytest.fixture
async def db_session(settings_override: None, monkeypatch: pytest.MonkeyPatch) -> AsyncSession:
    """PostgreSQL session with ORM tables created for alert tests."""
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "")
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "")
    get_settings.cache_clear()

    from app.core.database import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
    get_settings.cache_clear()


async def _seed_discrepancy(session: AsyncSession) -> ReconDiscrepancy:
    merchant = Merchant(id=uuid.uuid4(), name="Acme Payments")
    discrepancy = ReconDiscrepancy(
        id=uuid.uuid4(),
        merchant_id=merchant.id,
        webhook_event_id=uuid.uuid4(),
        stripe_event_id="evt_alert_test",
        severity="HIGH",
        description=(
            "payment_intent.succeeded webhook received but no settled "
            "BalanceTransaction found"
        ),
        detected_at=datetime(2024, 6, 1, 12, 0, tzinfo=UTC),
    )
    session.add(merchant)
    session.add(discrepancy)
    await session.flush()
    return discrepancy


@pytest.mark.asyncio
@respx.mock
async def test_slack_delivery_success_sets_delivered_at(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_WEBHOOK_URL", _SLACK_URL)
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "")
    get_settings.cache_clear()

    discrepancy = await _seed_discrepancy(db_session)
    respx.post(_SLACK_URL).mock(return_value=httpx.Response(200))

    await AlertDispatcher(scheduler=None).deliver(discrepancy.id, db_session)
    await db_session.commit()

    result = await db_session.execute(
        select(AlertDelivery).where(
            AlertDelivery.discrepancy_id == discrepancy.id,
            AlertDelivery.channel == CHANNEL_SLACK,
        )
    )
    delivery = result.scalar_one()
    assert delivery.delivered_at is not None
    assert delivery.attempt_count == 0
    assert delivery.last_error is None


@pytest.mark.asyncio
@respx.mock
async def test_slack_500_records_error_and_attempt_count(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_WEBHOOK_URL", _SLACK_URL)
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "")
    get_settings.cache_clear()

    discrepancy = await _seed_discrepancy(db_session)
    respx.post(_SLACK_URL).mock(return_value=httpx.Response(500, text="error"))

    await AlertDispatcher(scheduler=None).deliver(discrepancy.id, db_session)
    await db_session.commit()

    result = await db_session.execute(
        select(AlertDelivery).where(
            AlertDelivery.discrepancy_id == discrepancy.id,
            AlertDelivery.channel == CHANNEL_SLACK,
        )
    )
    delivery = result.scalar_one()
    assert delivery.delivered_at is None
    assert delivery.attempt_count == 1
    assert delivery.last_error is not None


@pytest.mark.asyncio
@respx.mock
async def test_generic_webhook_hmac_signature_is_verifiable(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "")
    monkeypatch.setenv("ALERT_WEBHOOK_URL", _WEBHOOK_URL)
    get_settings.cache_clear()

    discrepancy = await _seed_discrepancy(db_session)
    captured: dict[str, bytes | str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        captured["signature"] = request.headers.get("X-SnitchRoot-Signature", "")
        return httpx.Response(200)

    respx.post(_WEBHOOK_URL).mock(side_effect=_handler)

    await AlertDispatcher(scheduler=None).deliver(discrepancy.id, db_session)
    await db_session.commit()

    body = captured["body"]
    assert isinstance(body, bytes)
    signature = captured["signature"]
    assert isinstance(signature, str)
    assert verify_webhook_signature(body, _SIGNING_SECRET, signature)


@pytest.mark.asyncio
@respx.mock
async def test_delivery_not_retried_after_three_attempts(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_WEBHOOK_URL", _SLACK_URL)
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "")
    get_settings.cache_clear()

    discrepancy = await _seed_discrepancy(db_session)
    respx.post(_SLACK_URL).mock(return_value=httpx.Response(500, text="error"))

    mock_scheduler = MagicMock()
    dispatcher = AlertDispatcher(scheduler=mock_scheduler)

    for _ in range(3):
        await dispatcher.deliver(discrepancy.id, db_session)
        await db_session.commit()

    result = await db_session.execute(
        select(AlertDelivery).where(
            AlertDelivery.discrepancy_id == discrepancy.id,
            AlertDelivery.channel == CHANNEL_SLACK,
        )
    )
    delivery = result.scalar_one()
    assert delivery.attempt_count == 3
    assert mock_scheduler.add_job.call_count == 2

    mock_scheduler.reset_mock()
    await dispatcher.deliver(discrepancy.id, db_session)
    await db_session.commit()
    mock_scheduler.add_job.assert_not_called()
