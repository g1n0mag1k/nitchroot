"""Behavioral tests for reconciliation worker, scheduler, and job lifecycle."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from app.core.config import Environment, get_settings
from app.core.operational import is_passive_production_mode
from app.persistence.enums import JobFailureCategory, ReconciliationJobStatus
from app.persistence.models import (
    Merchant,
    ProbeResultRow,
    ReconciliationJob,
    ReconciliationResultRow,
    WebhookEventRow,
)
from app.probes.models import ProbeStatus
from app.reconciliation.exceptions import NonRetryableJobError, RetryableJobError
from app.reconciliation.job_service import claim_reconciliation_job, recover_stale_processing_jobs
from app.reconciliation.models import ReconciliationReport, ReconciliationStatus
from app.reconciliation.processor import process_reconciliation_job
from app.reconciliation.queue import RECONCILIATION_JOBS_QUEUE, enqueue_reconciliation_job
from app.webhooks.models import WebhookEventType
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from workers.reconciliation_worker import ReconciliationWorker
from workers.scheduler import ReconciliationScheduler


async def _seed_webhook_job(
    session: AsyncSession,
    *,
    merchant_id: uuid.UUID | None = None,
    with_probe: bool = False,
    job_status: ReconciliationJobStatus = ReconciliationJobStatus.PENDING,
) -> tuple[ReconciliationJob, Merchant]:
    merchant_id = merchant_id or uuid.uuid4()
    merchant = Merchant(id=merchant_id, name="test-merchant", external_ref=str(merchant_id))
    session.add(merchant)
    webhook = WebhookEventRow(
        merchant_id=merchant.id,
        stripe_event_id=f"evt_{uuid.uuid4().hex[:8]}",
        event_type=WebhookEventType.PAYMENT_INTENT_SUCCEEDED.value,
        livemode=False,
        api_version="2024-06-20",
        object_id="pi_test_shared",
        object_status="succeeded",
        amount=5000,
        currency="usd",
        metadata_json={"merchant_id": str(merchant.id)},
        stripe_created_at=datetime.now(tz=UTC),
    )
    session.add(webhook)
    await session.flush()
    job = ReconciliationJob(
        merchant_id=merchant.id,
        webhook_event_id=webhook.id,
        status=job_status,
    )
    session.add(job)
    await session.flush()

    if with_probe:
        session.add(
            ProbeResultRow(
                id=uuid.uuid4(),
                merchant_id=merchant.id,
                status=ProbeStatus.SUCCEEDED.value,
                stripe_intent_id=webhook.object_id,
                amount=5000,
                currency="usd",
                probed_at=datetime.now(tz=UTC),
            )
        )
        await session.flush()
    return job, merchant


@pytest.mark.asyncio
async def test_pending_job_processed_to_completed(
    integration_session_factory,
    integration_redis,
) -> None:
    async with integration_session_factory() as session:
        async with session.begin():
            job, _ = await _seed_webhook_job(session, with_probe=True)
            job_id = job.id
        await enqueue_reconciliation_job(integration_redis, job_id)

    worker = ReconciliationWorker(integration_session_factory, integration_redis)
    worker._shutdown.set()
    await worker._execute_job(job_id)

    async with integration_session_factory() as session:
        refreshed = await session.get(ReconciliationJob, job_id)
        assert refreshed is not None
        assert refreshed.status == ReconciliationJobStatus.COMPLETED
        result = (
            await session.execute(
                select(ReconciliationResultRow).where(ReconciliationResultRow.job_id == job_id)
            )
        ).scalar_one()
        assert result.status == ReconciliationStatus.MATCHED


@pytest.mark.asyncio
async def test_process_reconciliation_job_is_called(
    integration_session_factory,
) -> None:
    async with integration_session_factory() as session:
        async with session.begin():
            job, _ = await _seed_webhook_job(session, with_probe=False)
            job_id = job.id

    with patch(
        "workers.reconciliation_worker.process_reconciliation_job",
        new_callable=AsyncMock,
    ) as mock_process:
        mock_process.return_value = ReconciliationReport(
            merchant_id=uuid.uuid4(),
            status=ReconciliationStatus.WEBHOOK_ONLY,
        )
        worker = ReconciliationWorker(integration_session_factory, AsyncMock())
        await worker._execute_job(job_id)
        mock_process.assert_awaited_once()


@pytest.mark.asyncio
async def test_completed_job_not_processed_again(
    integration_session_factory,
    integration_redis,
) -> None:
    async with integration_session_factory() as session:
        async with session.begin():
            job, _ = await _seed_webhook_job(session, with_probe=True)
            job.status = ReconciliationJobStatus.COMPLETED
            job.completed_at = datetime.now(tz=UTC)
            session.add(
                ReconciliationResultRow(
                    job_id=job.id,
                    merchant_id=job.merchant_id,
                    status=ReconciliationStatus.MATCHED,
                )
            )
            job_id = job.id

    with patch(
        "workers.reconciliation_worker.process_reconciliation_job",
        new_callable=AsyncMock,
    ) as mock_process:
        worker = ReconciliationWorker(integration_session_factory, integration_redis)
        await worker._execute_job(job_id)
        mock_process.assert_not_called()


@pytest.mark.asyncio
async def test_duplicate_redis_delivery_does_not_duplicate_results(
    integration_session_factory,
    integration_redis,
) -> None:
    async with integration_session_factory() as session:
        async with session.begin():
            job, _ = await _seed_webhook_job(session, with_probe=True)
            job_id = job.id

    worker = ReconciliationWorker(integration_session_factory, integration_redis)
    await worker._execute_job(job_id)
    await worker._execute_job(job_id)

    async with integration_session_factory() as session:
        count = (
            await session.execute(
                select(func.count())
                .select_from(ReconciliationResultRow)
                .where(ReconciliationResultRow.job_id == job_id)
            )
        ).scalar_one()
        assert count == 1


@pytest.mark.asyncio
async def test_concurrent_workers_cannot_claim_same_job(integration_session_factory) -> None:
    async with integration_session_factory() as session:
        async with session.begin():
            job, _ = await _seed_webhook_job(session)
            job_id = job.id

    async def _claim() -> str:
        async with integration_session_factory() as session:
            async with session.begin():
                result = await claim_reconciliation_job(session, job_id)
                return result.reason

    reasons = await asyncio.gather(_claim(), _claim())
    assert reasons.count("claimed") == 1


@pytest.mark.asyncio
async def test_retryable_failure_returns_to_pending(
    integration_session_factory,
    integration_redis,
) -> None:
    async with integration_session_factory() as session:
        async with session.begin():
            job, _ = await _seed_webhook_job(session)
            job_id = job.id

    with patch(
        "workers.reconciliation_worker.process_reconciliation_job",
        new_callable=AsyncMock,
        side_effect=RetryableJobError("temporary database failure"),
    ):
        worker = ReconciliationWorker(integration_session_factory, integration_redis)
        await worker._execute_job(job_id)

    async with integration_session_factory() as session:
        refreshed = await session.get(ReconciliationJob, job_id)
        assert refreshed is not None
        assert refreshed.status == ReconciliationJobStatus.PENDING
        assert refreshed.failure_category == JobFailureCategory.RETRYABLE


@pytest.mark.asyncio
async def test_non_retryable_failure_becomes_failed(
    integration_session_factory,
    integration_redis,
) -> None:
    async with integration_session_factory() as session:
        async with session.begin():
            job, _ = await _seed_webhook_job(session)
            job_id = job.id

    with patch(
        "workers.reconciliation_worker.process_reconciliation_job",
        new_callable=AsyncMock,
        side_effect=NonRetryableJobError("missing merchant correlation"),
    ):
        worker = ReconciliationWorker(integration_session_factory, integration_redis)
        await worker._execute_job(job_id)

    async with integration_session_factory() as session:
        refreshed = await session.get(ReconciliationJob, job_id)
        assert refreshed is not None
        assert refreshed.status == ReconciliationJobStatus.FAILED
        assert refreshed.failure_category == JobFailureCategory.NON_RETRYABLE


@pytest.mark.asyncio
async def test_worker_exception_does_not_stop_processing(
    integration_session_factory,
    integration_redis,
) -> None:
    async with integration_session_factory() as session:
        async with session.begin():
            bad_job, _ = await _seed_webhook_job(session)
            good_job, _ = await _seed_webhook_job(session, with_probe=True)
            bad_id = bad_job.id
            good_id = good_job.id

    worker = ReconciliationWorker(integration_session_factory, integration_redis)

    async def _side_effect(session, job_id, *, engine=None):
        if job_id == bad_id:
            raise RuntimeError("simulated crash inside processor")
        return await process_reconciliation_job(session, job_id, engine=engine)

    with patch(
        "workers.reconciliation_worker.process_reconciliation_job",
        side_effect=_side_effect,
    ):
        await worker._execute_job(bad_id)
        await worker._execute_job(good_id)

    async with integration_session_factory() as session:
        good = await session.get(ReconciliationJob, good_id)
        assert good is not None
        assert good.status == ReconciliationJobStatus.COMPLETED


@pytest.mark.asyncio
async def test_stale_processing_job_recovered(integration_session_factory) -> None:
    async with integration_session_factory() as session:
        async with session.begin():
            job, _ = await _seed_webhook_job(session)
            job.status = ReconciliationJobStatus.PROCESSING
            job.processing_started_at = datetime.now(tz=UTC) - timedelta(minutes=30)
            job_id = job.id

    async with integration_session_factory() as session:
        async with session.begin():
            recovered = await recover_stale_processing_jobs(session)
        assert recovered == 1

    async with integration_session_factory() as session:
        refreshed = await session.get(ReconciliationJob, job_id)
        assert refreshed is not None
        assert refreshed.status == ReconciliationJobStatus.PENDING


@pytest.mark.asyncio
async def test_worker_restart_continues_pending_work(
    integration_session_factory,
    integration_redis,
) -> None:
    async with integration_session_factory() as session:
        async with session.begin():
            job, _ = await _seed_webhook_job(session, with_probe=True)
            job_id = job.id
        await enqueue_reconciliation_job(integration_redis, job_id)

    worker = ReconciliationWorker(integration_session_factory, integration_redis)
    await worker._execute_job(job_id)

    async with integration_session_factory() as session:
        refreshed = await session.get(ReconciliationJob, job_id)
        assert refreshed is not None
        assert refreshed.status == ReconciliationJobStatus.COMPLETED


@pytest.mark.asyncio
async def test_passive_production_mode_blocks_synthetic_probes(
    monkeypatch: pytest.MonkeyPatch,
    integration_session_factory,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", Environment.PRODUCTION.value)
    monkeypatch.setenv("SYNTHETIC_PROBE_ENABLED", "false")
    get_settings.cache_clear()
    settings = get_settings()
    assert is_passive_production_mode(settings) is True

    with patch("app.probes.stripe_prober.StripeProber.probe", new_callable=AsyncMock) as mock_probe:
        async with integration_session_factory() as session:
            async with session.begin():
                job, _ = await _seed_webhook_job(session, with_probe=False)
                await process_reconciliation_job(session, job.id)
        mock_probe.assert_not_called()


@pytest.mark.asyncio
async def test_scheduler_discovers_pending_jobs(
    integration_session_factory,
    integration_redis,
) -> None:
    async with integration_session_factory() as session:
        async with session.begin():
            job, _ = await _seed_webhook_job(session)
            job_id = job.id

    scheduler = ReconciliationScheduler(integration_session_factory, integration_redis)
    await scheduler.scan_pending_jobs()
    queued = await integration_redis.llen(RECONCILIATION_JOBS_QUEUE)
    assert queued >= 1
    popped = await integration_redis.rpop(RECONCILIATION_JOBS_QUEUE)
    assert popped == str(job_id)


@pytest.mark.asyncio
async def test_scheduler_does_not_enqueue_duplicate_jobs(
    integration_session_factory,
    integration_redis,
) -> None:
    async with integration_session_factory() as session:
        async with session.begin():
            await _seed_webhook_job(session)

    scheduler = ReconciliationScheduler(integration_session_factory, integration_redis)
    await scheduler.scan_pending_jobs()
    await scheduler.scan_pending_jobs()
    assert await integration_redis.llen(RECONCILIATION_JOBS_QUEUE) == 1


@pytest.mark.asyncio
async def test_worker_shutdown_drains_in_flight(integration_session_factory) -> None:
    worker = ReconciliationWorker(integration_session_factory, AsyncMock())
    worker._in_flight.add(asyncio.create_task(asyncio.sleep(0.01)))
    worker.request_shutdown()
    await worker._drain_in_flight()
    assert worker._shutdown.is_set()
