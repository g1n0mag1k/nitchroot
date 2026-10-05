"""Alert delivery orchestration for reconciliation discrepancies."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.alerts.channels import AlertChannel, GenericWebhookChannel, SlackChannel
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.models.alert_delivery import AlertDelivery
from app.models.recon_discrepancy import ReconDiscrepancy

logger = get_logger(__name__)

MAX_ALERT_ATTEMPTS = 3
RETRY_BASE_SECONDS = 30

CHANNEL_SLACK = "slack"
CHANNEL_WEBHOOK = "webhook"


def build_alert_channels(settings: Settings) -> list[tuple[str, AlertChannel]]:
    """Build enabled alert channels from environment-backed settings."""
    channels: list[tuple[str, AlertChannel]] = []
    if settings.slack_webhook_url:
        channels.append((CHANNEL_SLACK, SlackChannel(settings.slack_webhook_url)))
    if settings.alert_webhook_url:
        channels.append(
            (
                CHANNEL_WEBHOOK,
                GenericWebhookChannel(settings.alert_webhook_url, settings.secret_key),
            )
        )
    return channels


class AlertDispatcher:
    """Delivers discrepancy alerts and records per-channel delivery state."""

    def __init__(
        self,
        scheduler: AsyncIOScheduler | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._settings = settings

    def _settings_or_default(self) -> Settings:
        return self._settings or get_settings()

    async def deliver(self, discrepancy_id: UUID, db: AsyncSession) -> None:
        """Load discrepancy data, send alerts, and update delivery rows."""
        result = await db.execute(
            select(ReconDiscrepancy)
            .where(ReconDiscrepancy.id == discrepancy_id)
            .options(selectinload(ReconDiscrepancy.merchant))
        )
        discrepancy = result.scalar_one_or_none()
        if discrepancy is None:
            logger.warning("alert_discrepancy_not_found", discrepancy_id=str(discrepancy_id))
            return

        merchant = discrepancy.merchant
        channels = build_alert_channels(self._settings_or_default())
        if not channels:
            logger.info(
                "alert_no_channels_configured",
                discrepancy_id=str(discrepancy_id),
            )
            return

        retry_delay_seconds: int | None = None

        for channel_name, channel in channels:
            delivery = await self._get_or_create_delivery(db, discrepancy_id, channel_name)
            if delivery.delivered_at is not None:
                continue
            if delivery.attempt_count >= MAX_ALERT_ATTEMPTS:
                continue

            success = await channel.send(discrepancy, merchant)
            if success:
                delivery.delivered_at = datetime.now(UTC)
                delivery.last_error = None
                continue

            delivery.attempt_count += 1
            delivery.last_error = f"{channel_name} delivery failed with non-2xx or transport error"

            if delivery.attempt_count < MAX_ALERT_ATTEMPTS:
                delay = (2 ** (delivery.attempt_count - 1)) * RETRY_BASE_SECONDS
                retry_delay_seconds = (
                    delay
                    if retry_delay_seconds is None
                    else min(retry_delay_seconds, delay)
                )

        if retry_delay_seconds is not None and self._scheduler is not None:
            self._schedule_retry(discrepancy_id, retry_delay_seconds)

    async def _get_or_create_delivery(
        self,
        db: AsyncSession,
        discrepancy_id: UUID,
        channel_name: str,
    ) -> AlertDelivery:
        result = await db.execute(
            select(AlertDelivery).where(
                AlertDelivery.discrepancy_id == discrepancy_id,
                AlertDelivery.channel == channel_name,
            )
        )
        delivery = result.scalar_one_or_none()
        if delivery is None:
            delivery = AlertDelivery(
                discrepancy_id=discrepancy_id,
                channel=channel_name,
            )
            db.add(delivery)
            await db.flush()
        return delivery

    def _schedule_retry(self, discrepancy_id: UUID, delay_seconds: int) -> None:
        from workers.alert_jobs import run_alert_delivery

        run_at = datetime.now(UTC) + timedelta(seconds=delay_seconds)
        job_id = f"alert-delivery-{discrepancy_id}-{int(run_at.timestamp())}"
        self._scheduler.add_job(
            run_alert_delivery,
            trigger="date",
            run_date=run_at,
            id=job_id,
            args=[discrepancy_id],
            replace_existing=False,
        )
        logger.info(
            "alert_delivery_retry_scheduled",
            discrepancy_id=str(discrepancy_id),
            delay_seconds=delay_seconds,
        )
