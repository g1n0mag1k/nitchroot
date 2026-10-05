"""Redis delivery queue for reconciliation job identifiers."""

from __future__ import annotations

import uuid

import redis.asyncio as aioredis
import structlog

log = structlog.get_logger(__name__)

RECONCILIATION_JOBS_QUEUE = "snitchroot:recon:jobs"
QUEUED_JOB_MARKER_PREFIX = "snitchroot:recon:queued:"
_QUEUED_MARKER_TTL_SECONDS = 86_400


async def enqueue_reconciliation_job(
    redis_client: aioredis.Redis,  # type: ignore[type-arg]
    job_id: uuid.UUID,
    *,
    set_marker: bool = True,
) -> bool:
    """Push a durable job id onto the Redis queue.

    Returns:
        True if the job was newly enqueued (and marker set when requested).
        False if a queue marker already exists for this job id.
    """
    marker_key = f"{QUEUED_JOB_MARKER_PREFIX}{job_id}"
    if set_marker:
        marker_set = await redis_client.set(marker_key, "1", nx=True, ex=_QUEUED_MARKER_TTL_SECONDS)
        if marker_set is None:
            log.debug("reconciliation_job_enqueue_skipped_duplicate", job_id=str(job_id))
            return False

    await redis_client.lpush(RECONCILIATION_JOBS_QUEUE, str(job_id))
    log.info("reconciliation_job_enqueued", job_id=str(job_id))
    return True


async def dequeue_reconciliation_job(
    redis_client: aioredis.Redis,  # type: ignore[type-arg]
    timeout_seconds: int = 2,
) -> uuid.UUID | None:
    """Blocking pop of the next job id from the reconciliation queue."""
    result = await redis_client.brpop(RECONCILIATION_JOBS_QUEUE, timeout=timeout_seconds)
    if result is None:
        return None
    _queue_name, job_id_str = result
    return uuid.UUID(job_id_str)


async def clear_enqueue_marker(
    redis_client: aioredis.Redis,  # type: ignore[type-arg]
    job_id: uuid.UUID,
) -> None:
    """Remove the scheduler enqueue dedup marker (e.g. before retry re-enqueue)."""
    await redis_client.delete(f"{QUEUED_JOB_MARKER_PREFIX}{job_id}")
