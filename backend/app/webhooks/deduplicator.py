"""Redis-backed idempotency guard for Stripe webhook events.

Prevents double-processing events when Stripe retries delivery.
TTL: 24 hours (Stripe retries for up to 3 days; 24h covers normal delivery windows).
"""

from __future__ import annotations

import redis.asyncio as aioredis
import structlog

log = structlog.get_logger(__name__)

_DEDUP_TTL_SECONDS = 86_400  # 24 hours
_KEY_PREFIX = "snitchroot:webhook:seen:"


class WebhookDeduplicator:
    """Checks and marks Stripe event IDs in Redis to prevent reprocessing."""

    def __init__(self, redis_client: aioredis.Redis) -> None:  # type: ignore[type-arg]
        """Initialize with an async Redis client.

        Args:
            redis_client: Async Redis client used for SET NX dedup keys.
        """
        self._redis = redis_client

    async def is_duplicate(self, stripe_event_id: str) -> bool:
        """Return True if this event ID has already been processed.

        Args:
            stripe_event_id: The Stripe event ID (e.g. "evt_...").

        Returns:
            True if duplicate, False if first-time delivery.
        """
        key = f"{_KEY_PREFIX}{stripe_event_id}"
        result = await self._redis.set(key, "1", nx=True, ex=_DEDUP_TTL_SECONDS)
        is_dup = result is None  # SET NX returns None when key already existed
        if is_dup:
            log.warning("webhook_duplicate_detected", stripe_event_id=stripe_event_id)
        else:
            log.debug("webhook_event_marked_seen", stripe_event_id=stripe_event_id)
        return is_dup
