"""
Domain models for the Stripe probe engine.

All models are pure Python dataclasses / enums with no ORM dependency,
keeping the probe engine independently testable.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4


class ProbeStatus(StrEnum):
    """Terminal states for a single probe execution."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ProbeFailureMode(StrEnum):
    """
    Enumeration of every distinct failure mode the probe can detect.

    Each value maps directly to an alert payload so downstream consumers
    can route on failure type without string parsing.
    """

    AUTH_ERROR = "auth_error"
    CREATION_ERROR = "creation_error"
    SCHEMA_DRIFT = "schema_drift"
    CANCEL_ERROR = "cancel_error"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProbeRequest:
    """
    Input parameters for a single probe execution.

    Attributes:
        merchant_id: UUID of the merchant being probed.
        stripe_restricted_key: Restricted Stripe API key for this merchant.
        probe_id: Unique identifier for this probe run (auto-generated).
        currency: ISO 4217 currency code for the test PaymentIntent.
        amount: Amount in minor units (cents). Must be > 0.
        timeout_seconds: Maximum seconds to wait for Stripe API responses.
    """

    merchant_id: UUID
    stripe_restricted_key: str
    probe_id: UUID = field(default_factory=uuid4)
    currency: str = "usd"
    amount: int = 100
    timeout_seconds: int = 30


@dataclass(frozen=True)
class ProbeResult:
    """
    Immutable result of a single probe execution.

    Attributes:
        probe_id: Matches the ProbeRequest.probe_id.
        merchant_id: Matches the ProbeRequest.merchant_id.
        status: Terminal status of the probe.
        latency_ms: Wall-clock time from probe start to terminal state.
        probed_at: UTC timestamp when the probe was initiated.
        stripe_intent_id: Stripe PaymentIntent ID if creation succeeded.
        amount: Probe amount in minor units (cents). Used for reconciliation.
        currency: ISO 4217 currency code used for the probe.
        idempotency_key: Idempotency key sent to Stripe for this probe.
        raw_response_hash: SHA-256 hex digest of the Stripe response body.
            Never stores the raw response — only its hash.
        failure_mode: Set when status is FAILED.
        error_message: Human-readable error detail for alerting.
    """

    probe_id: UUID
    merchant_id: UUID
    status: ProbeStatus
    latency_ms: int
    probed_at: datetime
    stripe_intent_id: str | None = None
    amount: int = 100
    currency: str = "usd"
    idempotency_key: str | None = None
    raw_response_hash: str | None = None
    failure_mode: ProbeFailureMode | None = None
    error_message: str | None = None

    @property
    def succeeded(self) -> bool:
        """Return True if the probe completed without errors."""
        return self.status == ProbeStatus.SUCCEEDED

    @property
    def failed(self) -> bool:
        """Return True if the probe detected a failure."""
        return self.status == ProbeStatus.FAILED
