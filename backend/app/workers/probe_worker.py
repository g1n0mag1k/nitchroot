"""Background probe worker.

Runs zero-fee Stripe probes on a schedule for each configured merchant.
Idempotent: uses an in-memory set to skip merchants already being probed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Callable

import structlog

from app.probes.models import ProbeRequest, ProbeStatus
from app.probes.stripe_prober import StripeProber

from .models import JobRun, JobStatus

log = structlog.get_logger(__name__)

# In-memory lock set — merchant IDs currently being probed
_in_flight: set[str] = set()


async def run_probe_job(
    merchant_id: str,
    stripe_restricted_key: str,
    on_failure: Callable[[JobRun], None] | None = None,
) -> JobRun:
    """Execute one zero-fee probe for a single merchant.

    Args:
        merchant_id: Unique merchant identifier.
        stripe_restricted_key: Merchant's restricted Stripe key (rk_...).
        on_failure: Optional callback invoked synchronously on probe failure.

    Returns:
        A JobRun describing the outcome.
    """
    job_id = f"probe:{merchant_id}"
    if merchant_id in _in_flight:
        log.warning("probe_job_skipped_already_running", merchant_id=merchant_id)
        return JobRun(
            job_id=job_id,
            status=JobStatus.SKIPPED,
            merchant_id=merchant_id,
        )

    _in_flight.add(merchant_id)
    started_at = datetime.now(timezone.utc)
    log.info("probe_job_started", merchant_id=merchant_id)

    try:
        request = ProbeRequest(
            merchant_id=uuid.UUID(merchant_id),
            stripe_restricted_key=stripe_restricted_key,
        )
        prober = StripeProber()
        result = await prober.probe(request)
        finished_at = datetime.now(timezone.utc)

        if result.status == ProbeStatus.FAILED:
            log.error(
                "probe_job_failed",
                merchant_id=merchant_id,
                failure_mode=result.failure_mode,
                error=result.error_message,
            )
            run = JobRun(
                job_id=job_id,
                status=JobStatus.FAILED,
                started_at=started_at,
                finished_at=finished_at,
                error=result.error_message,
                merchant_id=merchant_id,
            )
            if on_failure:
                on_failure(run)
            return run

        log.info(
            "probe_job_succeeded",
            merchant_id=merchant_id,
            duration_ms=result.latency_ms,
        )
        return JobRun(
            job_id=job_id,
            status=JobStatus.SUCCEEDED,
            started_at=started_at,
            finished_at=finished_at,
            merchant_id=merchant_id,
        )
    except Exception as exc:
        finished_at = datetime.now(timezone.utc)
        log.exception("probe_job_exception", merchant_id=merchant_id, error=str(exc))
        return JobRun(
            job_id=job_id,
            status=JobStatus.FAILED,
            started_at=started_at,
            finished_at=finished_at,
            error=str(exc),
            merchant_id=merchant_id,
        )
    finally:
        _in_flight.discard(merchant_id)
