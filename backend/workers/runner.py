"""
APScheduler-based probe worker.

Loads merchants from PostgreSQL, schedules per-merchant interval probes with
jitter, and executes zero-fee Stripe probes behind a Redis distributed lock.
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

from app.core.config import get_settings
from app.core.crypto import decrypt_bytes
from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.probes.models import ProbeRequest, ProbeResult
from app.probes.stripe_prober import StripeProber
from apscheduler.jobstores.redis import RedisJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = get_logger(__name__)

_PROBE_LOCK_PREFIX = "snitchroot:probe_lock:"


def _redis_connect_args() -> dict[str, Any]:
    """Build redis-py connection keyword arguments from application settings."""
    settings = get_settings()
    parsed = urlparse(str(settings.redis_url))
    db = 0
    if parsed.path and parsed.path != "/":
        db = int(parsed.path.lstrip("/") or "0")
    connect_args: dict[str, Any] = {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 6379,
        "db": db,
    }
    if parsed.password:
        connect_args["password"] = parsed.password
    if parsed.username:
        connect_args["username"] = parsed.username
    return connect_args


def _build_jobstores() -> dict[str, RedisJobStore]:
    """Create APScheduler Redis job stores from settings."""
    return {"default": RedisJobStore(**_redis_connect_args())}


def _probe_interval_seconds(merchant: object) -> int:
    """Resolve probe interval for a merchant row."""
    settings = get_settings()
    interval = getattr(merchant, "probe_interval_seconds", None)
    if interval is None:
        return settings.default_probe_interval_seconds
    return int(interval)


def _interval_jitter_seconds(interval: int) -> float:
    """Return max jitter (seconds) — up to 10% of the interval."""
    return max(0.0, interval * 0.1)


def _lock_ttl_seconds(interval: int) -> int:
    """TTL for the distributed probe lock (interval minus 5 seconds)."""
    return max(1, interval - 5)


async def persist_probe_result(session: AsyncSession, result: ProbeResult) -> None:
    """
    Insert a domain ProbeResult into the probe_results table.

    Args:
        session: Active async SQLAlchemy session.
        result: Probe outcome from StripeProber.
    """
    from app.models.probe_result import ProbeResultRow

    row = ProbeResultRow(
        id=result.probe_id,
        merchant_id=result.merchant_id,
        status=result.status.value,
        failure_mode=result.failure_mode.value if result.failure_mode else None,
        stripe_intent_id=result.stripe_intent_id,
        idempotency_key=result.idempotency_key,
        latency_ms=result.latency_ms,
        raw_response_hash=result.raw_response_hash,
        probed_at=result.probed_at,
    )
    session.add(row)
    await session.commit()


async def execute_merchant_probe(
    merchant_id: UUID,
    *,
    redis: Redis | None = None,
    session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
    prober_factory: Callable[[], StripeProber] | None = None,
) -> None:
    """
    Run a single probe cycle for one merchant (scheduled job target).

    Acquires a Redis lock, loads the merchant, decrypts the Stripe key,
    calls StripeProber, and persists the result.
    """
    from app.models.merchant import Merchant

    settings = get_settings()
    redis_client = redis if redis is not None else await get_redis()
    prober_factory = prober_factory or StripeProber

    async with session_factory() as session:
        merchant = await session.get(Merchant, merchant_id)
        if merchant is None:
            log = logger.bind(merchant_id=str(merchant_id))
            log.warning("worker_probe_merchant_missing")
            return

        interval = _probe_interval_seconds(merchant)
        lock_key = f"{_PROBE_LOCK_PREFIX}{merchant_id}"
        lock_ttl = _lock_ttl_seconds(interval)

        acquired = await redis_client.set(lock_key, "1", nx=True, ex=lock_ttl)
        if not acquired:
            log = logger.bind(merchant_id=str(merchant_id))
            log.info("worker_probe_skipped_lock_held")
            return

        probe_id = uuid4()
        log = logger.bind(merchant_id=str(merchant_id), probe_id=str(probe_id))
        log.info("worker_probe_job_started")

        try:
            stripe_key = decrypt_bytes(
                merchant.stripe_restricted_key_enc,
                settings.secret_key,
            )
            request = ProbeRequest(
                merchant_id=merchant_id,
                stripe_restricted_key=stripe_key,
                probe_id=probe_id,
                timeout_seconds=settings.probe_timeout_seconds,
            )
            prober = prober_factory()
            result = await prober.probe(request)
            await persist_probe_result(session, result)
            log.info("worker_probe_job_succeeded", status=result.status.value)
        except Exception as exc:
            log.exception("worker_probe_job_failed", error=str(exc))
            raise


def schedule_merchant_probe(
    scheduler: AsyncIOScheduler,
    merchant: object,
) -> None:
    """Register or replace the interval job for a merchant."""
    merchant_id = merchant.id
    interval = _probe_interval_seconds(merchant)
    jitter = _interval_jitter_seconds(interval)
    scheduler.add_job(
        execute_merchant_probe,
        trigger=IntervalTrigger(seconds=interval, jitter=jitter),
        args=[merchant_id],
        id=f"merchant-probe-{merchant_id}",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )


async def load_merchants(session: AsyncSession) -> list[object]:
    """Return all merchants that should receive scheduled probes."""
    from app.models.merchant import Merchant

    result = await session.execute(select(Merchant))
    return list(result.scalars().all())


async def bootstrap_jobs(scheduler: AsyncIOScheduler) -> None:
    """Query merchants and register probe jobs on the scheduler."""
    async with AsyncSessionLocal() as session:
        merchants = await load_merchants(session)
    for merchant in merchants:
        schedule_merchant_probe(scheduler, merchant)
    logger.info("worker_jobs_scheduled", merchant_count=len(merchants))


def create_scheduler() -> AsyncIOScheduler:
    """Build an AsyncIOScheduler backed by Redis job storage."""
    return AsyncIOScheduler(jobstores=_build_jobstores())


async def run() -> None:
    """
    Start the probe worker until SIGTERM or SIGINT.

    Registers signal handlers for graceful scheduler shutdown.
    """
    scheduler = create_scheduler()
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def _request_shutdown() -> None:
        scheduler.shutdown(wait=False)
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _request_shutdown)

    scheduler.start()
    await bootstrap_jobs(scheduler)
    logger.info("worker_started")
    await shutdown_event.wait()
    logger.info("worker_stopped")


def main() -> None:
    """Console entrypoint."""
    asyncio.run(run())


if __name__ == "__main__":
    main()
