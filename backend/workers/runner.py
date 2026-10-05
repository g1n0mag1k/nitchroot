"""APScheduler worker: zero-fee probes and ledger reconciliation."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC

import structlog
from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.redis import get_redis
from app.models.probe_result import ProbeResult as ProbeResultRow
from app.probes.models import ProbeRequest, ProbeResult
from app.probes.stripe_prober import StripeProber
from app.reconciliation.job import run_reconciliation
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from redis.asyncio import Redis

log = structlog.get_logger(__name__)

_RECON_INTERVAL_SECONDS = 900  # 15 minutes


def probe_result_to_row(result: ProbeResult) -> ProbeResultRow:
    """Map a domain ``ProbeResult`` to a database row."""
    return ProbeResultRow(
        id=result.probe_id,
        merchant_id=result.merchant_id,
        status=result.status.value,
        failure_mode=result.failure_mode.value if result.failure_mode else None,
        stripe_intent_id=result.stripe_intent_id,
        idempotency_key=result.idempotency_key,
        latency_ms=result.latency_ms,
        raw_response_hash=result.raw_response_hash,
        amount=result.amount,
        currency=result.currency,
        probed_at=result.probed_at,
    )


async def run_probe(merchant_id: uuid.UUID, stripe_restricted_key: str) -> None:
    """Execute one probe cycle and persist the result."""
    settings = get_settings()
    prober = StripeProber()
    request = ProbeRequest(
        merchant_id=merchant_id,
        stripe_restricted_key=stripe_restricted_key,
        timeout_seconds=settings.probe_timeout_seconds,
    )
    result = await prober.probe(request)

    async with AsyncSessionLocal() as session:
        session.add(probe_result_to_row(result))
        await session.commit()

    log.info(
        "probe_persisted",
        merchant_id=str(merchant_id),
        probe_id=str(result.probe_id),
        status=result.status.value,
    )


async def run_reconciliation_for_merchant(merchant_id: uuid.UUID) -> None:
    """Scheduled reconciliation entrypoint for a single merchant."""
    settings = get_settings()
    redis: Redis = await get_redis()
    async with AsyncSessionLocal() as session:
        await run_reconciliation(merchant_id, session, redis, settings)


def _parse_merchant_ids(raw: str) -> list[uuid.UUID]:
    if not raw.strip():
        return []
    ids: list[uuid.UUID] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        ids.append(uuid.UUID(part))
    return ids


async def _bootstrap_scheduler(scheduler: AsyncIOScheduler) -> None:
    settings = get_settings()
    merchant_ids = _parse_merchant_ids(settings.worker_merchant_ids)
    stripe_key = settings.stripe_restricted_key

    for merchant_id in merchant_ids:
        scheduler.add_job(
            run_probe,
            "interval",
            seconds=settings.default_probe_interval_seconds,
            args=[merchant_id, stripe_key],
            id=f"probe-{merchant_id}",
            replace_existing=True,
        )
        scheduler.add_job(
            run_reconciliation_for_merchant,
            "interval",
            seconds=_RECON_INTERVAL_SECONDS,
            args=[merchant_id],
            id=f"recon-{merchant_id}",
            replace_existing=True,
        )


def main() -> None:
    """Start the worker process."""
    structlog.configure()
    scheduler = AsyncIOScheduler(timezone=UTC)
    asyncio.get_event_loop().run_until_complete(_bootstrap_scheduler(scheduler))
    scheduler.start()
    log.info("worker_started")
    try:
        asyncio.get_event_loop().run_forever()
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
