"""Post-match settlement verification against Stripe BalanceTransactions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.probes.balance_transaction_client import BalanceTransactionClient
from app.probes.models import ProbeResult
from app.webhooks.models import NormalizedWebhookEvent, WebhookEventType

from .engine import ReconciliationEngine
from .models import (
    DiscrepancySeverity,
    ReconciliationReport,
    ReconciliationStatus,
    ReconDiscrepancy,
)

_SETTLEMENT_DISCREPANCY_DESCRIPTION = (
    "payment_intent.succeeded webhook received but no settled BalanceTransaction "
    "found — funds may be stuck in dispute or refunded"
)


async def verify_settlement_after_match(
    report: ReconciliationReport,
    webhook: NormalizedWebhookEvent,
    stripe_restricted_key: str,
    bt_client: BalanceTransactionClient,
    settlement_lag_minutes: int,
    *,
    now: datetime | None = None,
) -> ReconDiscrepancy | None:
    """
    After a probe/webhook match, confirm funds settled in Stripe's ledger.

    Returns a HIGH severity discrepancy when no BalanceTransaction with
    ``net > 0`` exists and the webhook is older than ``settlement_lag_minutes``.
    Stripe API errors are soft-failed (no discrepancy; retry on next job run).
    """
    if report.status != ReconciliationStatus.MATCHED:
        return None
    if webhook.event_type != WebhookEventType.PAYMENT_INTENT_SUCCEEDED:
        return None

    clock = now or datetime.now(UTC)
    lag = timedelta(minutes=settlement_lag_minutes)
    webhook_age = clock - webhook.created_at

    check = await bt_client.check_for_source(
        stripe_restricted_key,
        webhook.stripe_event_id,
    )
    if not check.check_succeeded:
        return None
    if check.settled:
        return None
    if webhook_age < lag:
        return None

    return ReconDiscrepancy(
        merchant_id=report.merchant_id,
        webhook_event_id=webhook.internal_id,
        stripe_event_id=webhook.stripe_event_id,
        severity=DiscrepancySeverity.HIGH,
        description=_SETTLEMENT_DISCREPANCY_DESCRIPTION,
        probe_id=report.probe_id,
        stripe_intent_id=report.stripe_intent_id,
    )


async def reconcile_with_settlement_check(
    engine: ReconciliationEngine,
    probe: ProbeResult,
    webhook: NormalizedWebhookEvent,
    stripe_restricted_key: str,
    bt_client: BalanceTransactionClient,
    settlement_lag_minutes: int,
    *,
    now: datetime | None = None,
) -> tuple[ReconciliationReport, ReconDiscrepancy | None]:
    """Run probe/webhook reconciliation then optional settlement verification."""
    report = engine.reconcile(probe, webhook)
    discrepancy = await verify_settlement_after_match(
        report,
        webhook,
        stripe_restricted_key,
        bt_client,
        settlement_lag_minutes,
        now=now,
    )
    return report, discrepancy
