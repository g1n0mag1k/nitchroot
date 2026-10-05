"""Scheduled reconciliation between probe results and webhook events."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from redis.asyncio import Redis
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.probe_result import ProbeResult
from app.models.recon_discrepancy import ReconDiscrepancy
from app.models.webhook_event import WebhookEvent

log = structlog.get_logger(__name__)

PROBE_ONLY_DESCRIPTION = (
    "Probe fired but no matching payment_intent.succeeded webhook arrived within window"
)
WEBHOOK_ONLY_DESCRIPTION = "Webhook received but no probe result found in window"

_RECON_LOCK_TTL_SECONDS = 900  # 15 minutes


@dataclass(frozen=True)
class PendingDiscrepancy:
    """A discrepancy candidate before idempotency filtering."""

    merchant_id: uuid.UUID
    severity: str
    description: str
    stripe_intent_id: str | None
    probe_result_id: uuid.UUID | None
    webhook_event_id: uuid.UUID | None


def compute_pending_discrepancies(
    *,
    merchant_id: uuid.UUID,
    probes: list[ProbeResult],
    webhooks: list[WebhookEvent],
    now: datetime,
    settlement_lag_minutes: int,
) -> list[PendingDiscrepancy]:
    """Match probes and webhooks by payment intent id and emit gap records."""
    settlement_cutoff = now - timedelta(minutes=settlement_lag_minutes)

    webhooks_by_intent: dict[str, WebhookEvent] = {}
    for webhook in webhooks:
        intent_id = webhook.payment_intent_id
        if intent_id:
            webhooks_by_intent[intent_id] = webhook

    probes_by_intent: dict[str, ProbeResult] = {}
    for probe in probes:
        if probe.stripe_intent_id:
            probes_by_intent[probe.stripe_intent_id] = probe

    pending: list[PendingDiscrepancy] = []

    for intent_id, probe in probes_by_intent.items():
        if intent_id in webhooks_by_intent:
            continue
        if probe.probed_at >= settlement_cutoff:
            continue
        pending.append(
            PendingDiscrepancy(
                merchant_id=merchant_id,
                severity="HIGH",
                description=PROBE_ONLY_DESCRIPTION,
                stripe_intent_id=intent_id,
                probe_result_id=probe.id,
                webhook_event_id=None,
            )
        )

    for intent_id, webhook in webhooks_by_intent.items():
        if intent_id in probes_by_intent:
            continue
        pending.append(
            PendingDiscrepancy(
                merchant_id=merchant_id,
                severity="MEDIUM",
                description=WEBHOOK_ONLY_DESCRIPTION,
                stripe_intent_id=intent_id,
                probe_result_id=None,
                webhook_event_id=webhook.id,
            )
        )

    return pending


def _existing_key(
    row: ReconDiscrepancy,
) -> tuple[str, str | None, uuid.UUID | None, uuid.UUID | None]:
    return (row.severity, row.stripe_intent_id, row.probe_result_id, row.webhook_event_id)


def filter_new_discrepancies(
    pending: list[PendingDiscrepancy],
    existing: list[ReconDiscrepancy],
) -> list[PendingDiscrepancy]:
    """Drop candidates that already have an unresolved row with the same identity."""
    seen = {_existing_key(row) for row in existing}
    fresh: list[PendingDiscrepancy] = []
    for item in pending:
        key = (
            item.severity,
            item.stripe_intent_id,
            item.probe_result_id,
            item.webhook_event_id,
        )
        if key in seen:
            continue
        fresh.append(item)
        seen.add(key)
    return fresh


async def _load_window_rows(
    session: AsyncSession,
    merchant_id: uuid.UUID,
    window_start: datetime,
) -> tuple[list[ProbeResult], list[WebhookEvent], list[ReconDiscrepancy]]:
    probe_stmt = select(ProbeResult).where(
        and_(
            ProbeResult.merchant_id == merchant_id,
            ProbeResult.probed_at >= window_start,
        )
    )
    webhook_stmt = select(WebhookEvent).where(
        and_(
            WebhookEvent.merchant_id == merchant_id,
            WebhookEvent.received_at >= window_start,
        )
    )
    discrepancy_stmt = select(ReconDiscrepancy).where(
        and_(
            ReconDiscrepancy.merchant_id == merchant_id,
            ReconDiscrepancy.resolved_at.is_(None),
            ReconDiscrepancy.detected_at >= window_start,
        )
    )

    probe_result = await session.execute(probe_stmt)
    webhook_result = await session.execute(webhook_stmt)
    discrepancy_result = await session.execute(discrepancy_stmt)

    probes = list(probe_result.scalars().all())
    webhooks = list(webhook_result.scalars().all())
    existing = list(discrepancy_result.scalars().all())
    return probes, webhooks, existing


async def run_reconciliation(
    merchant_id: uuid.UUID,
    session: AsyncSession,
    redis: object,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> int:
    """Run reconciliation for one merchant; returns count of new discrepancies inserted."""
    now = now or datetime.now(UTC)
    lock_key = f"snitchroot:recon:lock:{merchant_id}"

    acquired = await redis.set(lock_key, "1", nx=True, ex=_RECON_LOCK_TTL_SECONDS)
    if not acquired:
        log.info("reconciliation_skipped_locked", merchant_id=str(merchant_id))
        return 0

    window_start = now - timedelta(hours=settings.recon_window_hours)
    probes, webhooks, existing = await _load_window_rows(session, merchant_id, window_start)

    pending = compute_pending_discrepancies(
        merchant_id=merchant_id,
        probes=probes,
        webhooks=webhooks,
        now=now,
        settlement_lag_minutes=settings.recon_settlement_lag_minutes,
    )
    to_insert = filter_new_discrepancies(pending, existing)

    for item in to_insert:
        session.add(
            ReconDiscrepancy(
                merchant_id=item.merchant_id,
                severity=item.severity,
                description=item.description,
                stripe_intent_id=item.stripe_intent_id,
                probe_result_id=item.probe_result_id,
                webhook_event_id=item.webhook_event_id,
                detected_at=now,
            )
        )

    if to_insert:
        await session.commit()
        log.info(
            "reconciliation_discrepancies_created",
            merchant_id=str(merchant_id),
            count=len(to_insert),
        )
    else:
        log.info("reconciliation_clean", merchant_id=str(merchant_id))

    return len(to_insert)
