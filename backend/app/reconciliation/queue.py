"""Redis-backed reconciliation job queue."""

from __future__ import annotations

import uuid

import structlog
from redis.asyncio import Redis

log = structlog.get_logger(__name__)

_QUEUE_KEY = "snitchroot:recon:jobs"


class ReconciliationJobQueue:
    """Minimal LPUSH/RPOP queue for reconciliation job IDs."""

    def __init__(self, redis_client: Redis) -> None:  # type: ignore[type-arg]
        self._redis = redis_client

    @property
    def queue_key(self) -> str:
        """Redis list key used for pending reconciliation jobs."""
        return _QUEUE_KEY

    async def enqueue(self, job_id: uuid.UUID) -> None:
        """Push a job ID onto the reconciliation queue."""
        await self._redis.lpush(_QUEUE_KEY, str(job_id))
        log.debug("reconciliation_job_enqueued", job_id=str(job_id))

    async def dequeue(self) -> uuid.UUID | None:
        """Pop the next job ID, or None if the queue is empty."""
        raw = await self._redis.rpop(_QUEUE_KEY)
        if raw is None:
            return None
        return uuid.UUID(str(raw))

    async def depth(self) -> int:
        """Return the number of pending jobs in the queue."""
        return int(await self._redis.llen(_QUEUE_KEY))
