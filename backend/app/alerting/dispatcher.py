"""Durable, idempotent alert dispatch."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.alerting.channels import (
    AlertChannel,
    PermanentAlertError,
    TransientAlertError,
    build_channels,
)
from app.alerting.payload import build_alert_payload
from app.core.config import Settings, get_settings
from app.persistence.enums import AlertChannel as AlertChannelEnum
from app.persistence.enums import AlertDeliveryStatus
from app.persistence.models import AlertDeliveryRow, ReconDiscrepancy

log = structlog.get_logger(__name__)


class AlertDispatcher:
    """Routes incident alerts to configured channels with durable delivery state."""

    def __init__(
        self,
        channels: list[AlertChannel] | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._channels = channels if channels is not None else build_channels(self._settings)

    async def dispatch_incident(
        self,
        session: AsyncSession,
        incident: ReconDiscrepancy,
    ) -> list[AlertDeliveryRow]:
        """Deliver alerts for an incident (idempotent per incident + channel)."""
        if not self._channels:
            log.info("alert_dispatch_skipped_no_channels", incident_id=str(incident.id))
            return []

        deliveries: list[AlertDeliveryRow] = []
        payload = build_alert_payload(incident)
        for channel in self._channels:
            delivery = await self._get_or_create_delivery(session, incident.id, channel.name)
            if delivery.status == AlertDeliveryStatus.SENT:
                continue
            if delivery.status == AlertDeliveryStatus.FAILED:
                continue
            if delivery.attempt_count >= self._settings.alert_max_attempts:
                continue

            try:
                await self._send_with_retry(channel, payload)
            except PermanentAlertError as exc:
                await self._record_failure(
                    session,
                    delivery,
                    error=str(exc),
                    status=AlertDeliveryStatus.FAILED,
                )
            except TransientAlertError as exc:
                await self._record_failure(
                    session,
                    delivery,
                    error=str(exc),
                    status=AlertDeliveryStatus.RETRYABLE,
                )
            except Exception as exc:
                await self._record_failure(
                    session,
                    delivery,
                    error=f"{type(exc).__name__}",
                    status=AlertDeliveryStatus.RETRYABLE,
                )
            else:
                await self._record_success(session, delivery)
            deliveries.append(delivery)
        return deliveries

    async def _get_or_create_delivery(
        self,
        session: AsyncSession,
        incident_id: uuid.UUID,
        channel_name: str,
    ) -> AlertDeliveryRow:
        channel_enum = AlertChannelEnum(channel_name)
        stmt = insert(AlertDeliveryRow).values(
            incident_id=incident_id,
            channel=channel_enum,
            status=AlertDeliveryStatus.PENDING,
            attempt_count=0,
        )
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["incident_id", "channel"],
        )
        await session.execute(stmt)
        await session.flush()
        existing = (
            await session.execute(
                select(AlertDeliveryRow).where(
                    AlertDeliveryRow.incident_id == incident_id,
                    AlertDeliveryRow.channel == channel_enum,
                )
            )
        ).scalar_one()
        return existing

    async def _record_success(
        self,
        session: AsyncSession,
        delivery: AlertDeliveryRow,
    ) -> None:
        now = datetime.now(tz=UTC)
        delivery.status = AlertDeliveryStatus.SENT
        delivery.delivered_at = now
        delivery.last_attempt_at = now
        delivery.attempt_count += 1
        delivery.last_error = None
        await session.flush()
        log.info(
            "alert_delivery_sent",
            delivery_id=str(delivery.id),
            channel=delivery.channel.value,
        )

    async def _record_failure(
        self,
        session: AsyncSession,
        delivery: AlertDeliveryRow,
        *,
        error: str,
        status: AlertDeliveryStatus,
    ) -> None:
        now = datetime.now(tz=UTC)
        delivery.status = status
        delivery.last_attempt_at = now
        delivery.attempt_count += 1
        delivery.last_error = error[:2000]
        await session.flush()
        log.warning(
            "alert_delivery_failed",
            delivery_id=str(delivery.id),
            channel=delivery.channel.value,
            status=status.value,
            error=error[:200],
        )

    async def _send_with_retry(self, channel: AlertChannel, payload: object) -> None:
        @retry(
            retry=retry_if_exception_type(TransientAlertError),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.2, min=0.2, max=2),
            reraise=True,
        )
        async def _attempt() -> None:
            await channel.send(payload)  # type: ignore[arg-type]

        await _attempt()
