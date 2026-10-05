"""Background jobs for alert delivery."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.alerts.dispatcher import AlertDispatcher
from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger

from workers.scheduler import get_scheduler

logger = get_logger(__name__)


async def run_alert_delivery(discrepancy_id: UUID) -> None:
    """APScheduler entrypoint: attempt alert delivery for one discrepancy."""
    scheduler = get_scheduler()
    dispatcher = AlertDispatcher(scheduler=scheduler)
    async with AsyncSessionLocal() as session:
        try:
            await dispatcher.deliver(discrepancy_id, session)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception(
                "alert_delivery_job_failed",
                discrepancy_id=str(discrepancy_id),
            )
            raise


def enqueue_alert_delivery(discrepancy_id: UUID) -> None:
    """Schedule immediate alert delivery on the worker scheduler."""
    scheduler = get_scheduler()
    if scheduler is None:
        logger.warning(
            "alert_delivery_not_scheduled_no_scheduler",
            discrepancy_id=str(discrepancy_id),
        )
        return

    run_at = datetime.now(UTC)
    scheduler.add_job(
        run_alert_delivery,
        trigger="date",
        run_date=run_at,
        id=f"alert-delivery-{discrepancy_id}",
        args=[discrepancy_id],
        replace_existing=True,
    )
