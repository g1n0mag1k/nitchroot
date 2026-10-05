"""Tests for background workers and scheduler.

All Stripe calls and scheduler interactions are mocked.
No live network, DB, or Redis calls.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.probes.models import ProbeFailureMode, ProbeResult, ProbeStatus
from app.workers.models import JobRun, JobStatus
from app.workers.probe_worker import run_probe_job

# ── helpers ───────────────────────────────────────────────────────────────────

_MERCHANT_ID = str(uuid.uuid4())
_RK = "rk_test_placeholder_do_not_use"


def _make_probe_result(status: ProbeStatus = ProbeStatus.SUCCEEDED) -> ProbeResult:
    return ProbeResult(
        probe_id=uuid.uuid4(),
        merchant_id=uuid.UUID(_MERCHANT_ID),
        status=status,
        probed_at=datetime.now(timezone.utc),
        latency_ms=42,
        stripe_intent_id="pi_test_001" if status == ProbeStatus.SUCCEEDED else None,
        amount=100,
        currency="usd",
        idempotency_key="snitchroot-probe-test",
        failure_mode=ProbeFailureMode.AUTH_ERROR if status == ProbeStatus.FAILED else None,
        error_message="No such API key" if status == ProbeStatus.FAILED else None,
    )


# ── JobRun model tests ────────────────────────────────────────────────────────


class TestJobRun:
    def test_duration_ms_none_when_not_finished(self) -> None:
        run = JobRun(job_id="test", status=JobStatus.RUNNING)
        assert run.duration_ms is None

    def test_duration_ms_computed_when_finished(self) -> None:
        from datetime import timedelta

        start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        end = start + timedelta(milliseconds=500)
        run = JobRun(
            job_id="test",
            status=JobStatus.SUCCEEDED,
            started_at=start,
            finished_at=end,
        )
        assert abs(run.duration_ms - 500.0) < 1.0

    def test_job_run_is_frozen(self) -> None:
        run = JobRun(job_id="test")
        with pytest.raises((AttributeError, TypeError)):
            run.status = JobStatus.FAILED  # type: ignore[misc]

    def test_default_status_is_pending(self) -> None:
        run = JobRun(job_id="test")
        assert run.status == JobStatus.PENDING


# ── Probe worker tests ─────────────────────────────────────────────────────────


class TestProbeWorker:
    def setup_method(self) -> None:
        # Clear in-flight set between tests
        from app.workers import probe_worker

        probe_worker._in_flight.clear()

    @pytest.mark.asyncio
    async def test_successful_probe_returns_succeeded(self) -> None:
        with patch("app.workers.probe_worker.StripeProber") as mock_prober_cls:
            mock_prober = AsyncMock()
            mock_prober.probe = AsyncMock(
                return_value=_make_probe_result(ProbeStatus.SUCCEEDED)
            )
            mock_prober_cls.return_value = mock_prober
            run = await run_probe_job(_MERCHANT_ID, _RK)

        assert run.status == JobStatus.SUCCEEDED
        assert run.merchant_id == _MERCHANT_ID
        assert run.error is None
        assert run.finished_at is not None

    @pytest.mark.asyncio
    async def test_failed_probe_returns_failed(self) -> None:
        with patch("app.workers.probe_worker.StripeProber") as mock_prober_cls:
            mock_prober = AsyncMock()
            mock_prober.probe = AsyncMock(
                return_value=_make_probe_result(ProbeStatus.FAILED)
            )
            mock_prober_cls.return_value = mock_prober
            run = await run_probe_job(_MERCHANT_ID, _RK)

        assert run.status == JobStatus.FAILED
        assert run.error is not None

    @pytest.mark.asyncio
    async def test_on_failure_callback_called(self) -> None:
        callback = MagicMock()
        with patch("app.workers.probe_worker.StripeProber") as mock_prober_cls:
            mock_prober = AsyncMock()
            mock_prober.probe = AsyncMock(
                return_value=_make_probe_result(ProbeStatus.FAILED)
            )
            mock_prober_cls.return_value = mock_prober
            await run_probe_job(_MERCHANT_ID, _RK, on_failure=callback)

        callback.assert_called_once()
        assert callback.call_args[0][0].status == JobStatus.FAILED

    @pytest.mark.asyncio
    async def test_on_failure_not_called_on_success(self) -> None:
        callback = MagicMock()
        with patch("app.workers.probe_worker.StripeProber") as mock_prober_cls:
            mock_prober = AsyncMock()
            mock_prober.probe = AsyncMock(
                return_value=_make_probe_result(ProbeStatus.SUCCEEDED)
            )
            mock_prober_cls.return_value = mock_prober
            await run_probe_job(_MERCHANT_ID, _RK, on_failure=callback)

        callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_if_already_in_flight(self) -> None:
        from app.workers import probe_worker

        probe_worker._in_flight.add(_MERCHANT_ID)
        run = await run_probe_job(_MERCHANT_ID, _RK)
        assert run.status == JobStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_in_flight_cleared_after_success(self) -> None:
        from app.workers import probe_worker

        with patch("app.workers.probe_worker.StripeProber") as mock_prober_cls:
            mock_prober = AsyncMock()
            mock_prober.probe = AsyncMock(
                return_value=_make_probe_result(ProbeStatus.SUCCEEDED)
            )
            mock_prober_cls.return_value = mock_prober
            await run_probe_job(_MERCHANT_ID, _RK)

        assert _MERCHANT_ID not in probe_worker._in_flight

    @pytest.mark.asyncio
    async def test_in_flight_cleared_after_exception(self) -> None:
        from app.workers import probe_worker

        with patch("app.workers.probe_worker.StripeProber") as mock_prober_cls:
            mock_prober = AsyncMock()
            mock_prober.probe = AsyncMock(side_effect=RuntimeError("unexpected crash"))
            mock_prober_cls.return_value = mock_prober
            run = await run_probe_job(_MERCHANT_ID, _RK)

        assert run.status == JobStatus.FAILED
        assert _MERCHANT_ID not in probe_worker._in_flight

    @pytest.mark.asyncio
    async def test_exception_captured_in_error_field(self) -> None:
        with patch("app.workers.probe_worker.StripeProber") as mock_prober_cls:
            mock_prober = AsyncMock()
            mock_prober.probe = AsyncMock(side_effect=RuntimeError("boom"))
            mock_prober_cls.return_value = mock_prober
            run = await run_probe_job(_MERCHANT_ID, _RK)

        assert "boom" in (run.error or "")


# ── Scheduler tests ───────────────────────────────────────────────────────────


class TestScheduler:
    def test_build_scheduler_returns_scheduler(self) -> None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        from app.workers.scheduler import build_scheduler

        scheduler = build_scheduler()
        assert isinstance(scheduler, AsyncIOScheduler)

    def test_scheduler_has_two_jobs(self) -> None:
        from app.workers.scheduler import build_scheduler

        scheduler = build_scheduler()
        jobs = scheduler.get_jobs()
        assert len(jobs) == 2

    def test_scheduler_job_ids(self) -> None:
        from app.workers.scheduler import build_scheduler

        scheduler = build_scheduler()
        job_ids = {job.id for job in scheduler.get_jobs()}
        assert "probe_heartbeat" in job_ids
        assert "reconciliation_sweep" in job_ids

    @pytest.mark.asyncio
    async def test_lifespan_starts_and_stops_scheduler(self) -> None:
        from fastapi import FastAPI

        from app.workers.scheduler import scheduler_lifespan

        app = FastAPI()
        with patch("app.workers.scheduler.build_scheduler") as mock_build:
            mock_scheduler = MagicMock()
            mock_scheduler.get_jobs.return_value = []
            mock_build.return_value = mock_scheduler
            async with scheduler_lifespan(app):
                mock_scheduler.start.assert_called_once()
            mock_scheduler.shutdown.assert_called_once_with(wait=False)

    @pytest.mark.asyncio
    async def test_probe_heartbeat_logs(self) -> None:
        from app.workers.scheduler import _probe_heartbeat

        await _probe_heartbeat()

    @pytest.mark.asyncio
    async def test_reconciliation_sweep_logs(self) -> None:
        from app.workers.scheduler import _reconciliation_sweep

        await _reconciliation_sweep()
