"""Tests for merchant discovery in the probe scheduler."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from app.models.merchant import Merchant
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from workers.scheduler import MERCHANT_REFRESH_INTERVAL_SECONDS, ProbeScheduler


@pytest.mark.asyncio
async def test_refresh_registers_new_merchants_idempotently(db_engine) -> None:
    session_factory = async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )
    async with session_factory() as db_session:
        merchant = Merchant(
            name="Scheduled Merchant",
            stripe_restricted_key_enc="enc",
            probe_interval_seconds=120,
        )
        db_session.add(merchant)
        await db_session.commit()
        merchant_id = merchant.id

    runner = AsyncMock()
    scheduler = ProbeScheduler(probe_runner=runner)
    scheduler.start()

    with (
        patch("workers.scheduler.get_session_factory", return_value=session_factory),
        patch.object(
            scheduler,
            "_schedule_merchant",
            wraps=scheduler._schedule_merchant,
        ) as mock_schedule,
    ):
        await scheduler.refresh_merchants()
        await scheduler.refresh_merchants()

        assert mock_schedule.call_count == 1
        mock_schedule.assert_called_with(merchant_id, 120)

    scheduler.shutdown()


def test_merchant_refresh_interval_is_five_minutes() -> None:
    assert MERCHANT_REFRESH_INTERVAL_SECONDS == 300
