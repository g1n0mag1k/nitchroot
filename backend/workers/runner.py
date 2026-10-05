"""
APScheduler-based probe worker.

Loads merchants from PostgreSQL, schedules per-merchant interval probes with
jitter, and executes zero-fee Stripe probes behind a Redis distributed lock.
"""

from __future__ import annotations

import asyncio
import signal
from urllib.parse import urlparse
from uuid import UUID

import redis.asyncio as aioredis
from app.core.config import get_settings
from app.core.crypto import decrypt_bytes
from app.core.database import AsyncSessionLocal
from app.core.logging import configure_logging, get_logger
from app.core.redis import _get_redis_pool
from app.models.merchant import Merchant, ProbeResultRecord
from app.probes.models import ProbeRequest, ProbeResult
from app.probes.stripe_prober import StripeProber
from apscheduler.jobstores.redis import RedisJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

logger = get_logger(__name__)

_LOCK_KEY_PREFIX = "snitchroot:probe_lock:"
_JOB_ID_PREFIX = "merchant-probe-"


def _redis_jobstore() -> dict[str, RedisJobStore]:
    """Build APScheduler Redis job store from application settings."""
    settings = get_settings()
    parsed = urlparse(str(settings.redis_url))
    db = 0
    if parsed.path and parsed.path != "/":
        db = int(parsed.path.lstrip("/") or 0)
    connect_args: dict[str, object] = {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 6379,
        "db": db,
    }
    if parsed.password:
        connect_args["password"] = parsed.password
    return {
        "default": RedisJobStore(
            jobs_key="snitchroot.apscheduler.jobs",
            run_times_key="snitchroot.apscheduler.run_times",
            **connect_args,
        )
    }


def create_scheduler() -> AsyncIOScheduler:
    """Return a configured AsyncIOScheduler with Redis-backed job persistence."""
    return AsyncIOScheduler(jobstores=_redis_jobstore())


def _lock_key(merchant_id: UUID) -> str:
    return f"{_LOCK_KEY_PREFIX}{merchant_id}"


async def try_acquire_probe_lock(
    redis: aioredis.Redis,  # type: ignore[type-arg]
    merchant_id: UUID,
    interval_seconds: int,
) -> bool:
    """
    Acquire a distributed probe lock (SET NX with TTL = interval - 5s).

    Returns:
        True if this worker may run the probe, False if another pod holds the lock.
    """
    ttl = max(1, interval_seconds - 5)
    acquired = await redis.set(_lock_key(merchant_id), "1", nx=True, ex=ttl)
    return acquired is not None and acquired is not False


def probe_result_to_record(result: ProbeResult) -> ProbeResultRecord:
    """Map a domain ProbeResult to the SQLAlchemy probe_results row."""
    return ProbeResultRecord(
        id=result.probe_id,
        merchant_id=result.merchant_id,
        status=result.status.value,
        failure_mode=result.failure_mode.value if result.failure_mode else None,
        stripe_intent_id=result.stripe_intent_id,
        idempotency_key=result.idempotency_key,
        latency_ms=result.latency_ms,
        raw_response_hash=result.raw_response_hash,
        probed_at=result.probed_at,
        amount=result.amount,
        currency=result.currency,
        error_message=result.error_message,
    )


async def persist_probe_result(session: object, result: ProbeResult) -> None:
    """Persist a probe result row and commit the session."""
    record = probe_result_to_record(result)
    session.add(record)  # type: ignore[attr-defined]
    await session.commit()  # type: ignore[attr-defined]


async def execute_merchant_probe(
    merchant_id: UUID,
    *,
    redis: aioredis.Redis | None = None,  # type: ignore[type-arg]
    prober: StripeProber | None = None,
) -> None:
    """
    Run a single merchant probe cycle (lock, Stripe probe, persist).

    Intended for APScheduler jobs and unit tests.
    """
    settings = get_settings()
    if redis is None:
        redis = aioredis.Redis(connection_pool=_get_redis_pool(), decode_responses=True)
    if prober is None:
        prober = StripeProber()

    async with AsyncSessionLocal() as session:
        merchant = await session.get(Merchant, merchant_id)
        if merchant is None:
            log = logger.bind(merchant_id=str(merchant_id))
            log.warning("probe_merchant_not_found")
            return

        interval_seconds = (
            merchant.probe_interval_seconds or settings.default_probe_interval_seconds
        )

        if not await try_acquire_probe_lock(redis, merchant_id, interval_seconds):
            logger.bind(merchant_id=str(merchant_id)).info("probe_skipped_lock_held")
            return

        stripe_key = decrypt_bytes(merchant.stripe_restricted_key_enc, settings.secret_key)
        request = ProbeRequest(
            merchant_id=merchant_id,
            stripe_restricted_key=stripe_key,
            timeout_seconds=settings.probe_timeout_seconds,
        )
        log = logger.bind(
            merchant_id=str(merchant_id),
            probe_id=str(request.probe_id),
        )
        log.info("probe_job_started")

        try:
            result = await prober.probe(request)
            await persist_probe_result(session, result)
            log.info("probe_job_succeeded", status=result.status.value)
        except Exception:
            log.exception("probe_job_failed")
            raise


async def schedule_merchant_probes(scheduler: AsyncIOScheduler) -> None:
    """Load all merchants and register interval probe jobs with jitter."""
    settings = get_settings()
    async with AsyncSessionLocal() as session:
        rows = await session.execute(select(Merchant))
        merchants = rows.scalars().all()

    for merchant in merchants:
        interval_seconds = (
            merchant.probe_interval_seconds or settings.default_probe_interval_seconds
        )
        jitter = max(1.0, interval_seconds * 0.1)
        scheduler.add_job(
            execute_merchant_probe,
            trigger=IntervalTrigger(seconds=interval_seconds, jitter=jitter),
            args=[merchant.id],
            id=f"{_JOB_ID_PREFIX}{merchant.id}",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info(
            "probe_job_scheduled",
            merchant_id=str(merchant.id),
            interval_seconds=interval_seconds,
            jitter_seconds=jitter,
        )


async def _run_until_signal(scheduler: AsyncIOScheduler) -> None:
    """Start the scheduler, load jobs, and block until SIGTERM/SIGINT."""
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_shutdown() -> None:
        scheduler.shutdown(wait=False)
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _request_shutdown)

    scheduler.start()
    await schedule_merchant_probes(scheduler)
    logger.info("probe_worker_started", job_count=len(scheduler.get_jobs()))
    await stop_event.wait()
    logger.info("probe_worker_stopped")


async def async_main() -> None:
    """Worker entrypoint."""
    configure_logging()
    scheduler = create_scheduler()
    try:
        await _run_until_signal(scheduler)
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)


def main() -> None:
    """CLI entrypoint for ``python -m workers.runner``."""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
