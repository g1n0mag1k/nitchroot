"""
APScheduler worker entrypoint.

Runs scheduled probe and reconciliation jobs. Reconciliation matches probe
results to payment_intent.succeeded webhooks, then verifies Stripe ledger
settlement via BalanceTransaction lookups.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.probes.balance_transaction_client import BalanceTransactionClient
from app.probes.models import ProbeResult
from app.reconciliation.engine import ReconciliationEngine
from app.reconciliation.models import (
    ReconciliationReport,
    ReconciliationStatus,
    ReconDiscrepancy,
)
from app.reconciliation.settlement import reconcile_with_settlement_check
from app.webhooks.models import NormalizedWebhookEvent
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = get_logger(__name__)


@dataclass(frozen=True)
class ReconciliationWorkItem:
    """A probe/webhook pair plus the merchant key for Stripe ledger checks."""

    probe: ProbeResult
    webhook: NormalizedWebhookEvent
    stripe_restricted_key: str


class ReconciliationPairSource(Protocol):
    """Loads pending probe/webhook pairs for the reconciliation job."""

    async def fetch_pairs(self) -> list[ReconciliationWorkItem]:
        """Return work items ready for reconciliation."""
        ...


class EmptyReconciliationPairSource:
    """Placeholder until ORM-backed pair loading is wired."""

    async def fetch_pairs(self) -> list[ReconciliationWorkItem]:
        return []


class InMemoryReconciliationPairSource:
    """Test and bootstrap source that returns a fixed list of pairs."""

    def __init__(self, items: list[ReconciliationWorkItem]) -> None:
        self._items = items

    async def fetch_pairs(self) -> list[ReconciliationWorkItem]:
        return list(self._items)


async def process_reconciliation_item(
    item: ReconciliationWorkItem,
    engine: ReconciliationEngine,
    bt_client: BalanceTransactionClient,
    settlement_lag_minutes: int,
    *,
    now: datetime | None = None,
) -> tuple[ReconciliationReport, ReconDiscrepancy | None]:
    """Match probe to webhook and verify BalanceTransaction settlement."""
    report, discrepancy = await reconcile_with_settlement_check(
        engine,
        item.probe,
        item.webhook,
        item.stripe_restricted_key,
        bt_client,
        settlement_lag_minutes,
        now=now,
    )
    return report, discrepancy


async def run_reconciliation_job(
    pair_source: ReconciliationPairSource,
    engine: ReconciliationEngine | None = None,
    bt_client: BalanceTransactionClient | None = None,
    *,
    now: datetime | None = None,
) -> list[ReconDiscrepancy]:
    """
    Execute one reconciliation sweep.

    Persists discrepancies via the pair source in a future ORM layer; for now
    returns newly detected ReconDiscrepancy records for callers to store.
    """
    settings = get_settings()
    settlement_lag = settings.settlement_lag_minutes
    recon_engine = engine or ReconciliationEngine(
        window_seconds=settings.recon_window_hours * 3600,
    )
    bt = bt_client or BalanceTransactionClient()
    clock = now or datetime.now(UTC)

    created: list[ReconDiscrepancy] = []
    pairs = await pair_source.fetch_pairs()

    for item in pairs:
        report, settlement_discrepancy = await process_reconciliation_item(
            item,
            recon_engine,
            bt,
            settlement_lag,
            now=clock,
        )
        if report.status == ReconciliationStatus.MATCHED and settlement_discrepancy:
            logger.warning(
                "recon_settlement_discrepancy",
                merchant_id=str(settlement_discrepancy.merchant_id),
                stripe_event_id=settlement_discrepancy.stripe_event_id,
                severity=str(settlement_discrepancy.severity),
            )
            created.append(settlement_discrepancy)

    return created


async def _scheduled_reconciliation() -> None:
    """APScheduler callback: one reconciliation sweep."""
    await run_reconciliation_job(EmptyReconciliationPairSource())


def main() -> None:
    """Start the worker scheduler (blocks until interrupted)."""
    configure_logging()
    settings = get_settings()
    log = logger.bind(environment=settings.environment.value)
    log.info("worker_starting")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    scheduler = AsyncIOScheduler(event_loop=loop, timezone="UTC")
    scheduler.add_job(
        _scheduled_reconciliation,
        trigger="interval",
        minutes=5,
        id="reconciliation",
        replace_existing=True,
    )
    scheduler.start()

    try:
        loop.run_forever()
    except (KeyboardInterrupt, SystemExit):
        log.info("worker_shutting_down")
        scheduler.shutdown(wait=False)
    finally:
        loop.close()


if __name__ == "__main__":
    main()
