"""APScheduler integration: per-merchant probe jobs with periodic discovery."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.core.database import get_session_factory
from app.core.logging import get_logger
from app.models.merchant import Merchant
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

logger = get_logger(__name__)

MERCHANT_REFRESH_INTERVAL_SECONDS = 300


class ProbeScheduler:
    """Schedules Stripe probes and picks up new merchants every 5 minutes."""

    def __init__(
        self,
        probe_runner: Callable[[UUID], Coroutine[None, None, None]] | None = None,
    ) -> None:
        self._scheduler = AsyncIOScheduler()
        self._scheduled_merchant_ids: set[UUID] = set()
        self._probe_runner = probe_runner or self._default_probe_runner

    async def _default_probe_runner(self, merchant_id: UUID) -> None:
        logger.info("probe_scheduled_tick", merchant_id=str(merchant_id))

    async def refresh_merchants(self) -> None:
        """Load active merchants from DB and register any not yet scheduled."""
        async with get_session_factory()() as session:
            result = await session.execute(select(Merchant).where(Merchant.is_active.is_(True)))
            merchants = result.scalars().all()

        for merchant in merchants:
            if merchant.id in self._scheduled_merchant_ids:
                continue
            self._schedule_merchant(merchant.id, merchant.probe_interval_seconds)
            self._scheduled_merchant_ids.add(merchant.id)
            logger.info(
                "merchant_probe_scheduled",
                merchant_id=str(merchant.id),
                interval_seconds=merchant.probe_interval_seconds,
            )

    def _schedule_merchant(self, merchant_id: UUID, interval_seconds: int) -> None:
        self._scheduler.add_job(
            self._run_probe,
            trigger="interval",
            seconds=interval_seconds,
            id=f"probe:{merchant_id}",
            args=[merchant_id],
            replace_existing=True,
            max_instances=1,
        )

    async def _run_probe(self, merchant_id: UUID) -> None:
        await self._probe_runner(merchant_id)

    def start(self) -> None:
        self._scheduler.add_job(
            self.refresh_merchants,
            trigger="interval",
            seconds=MERCHANT_REFRESH_INTERVAL_SECONDS,
            id="merchant_refresh",
            replace_existing=True,
        )
        self._scheduler.start()

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)
