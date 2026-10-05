"""Shared persistence-layer enumerations."""

from __future__ import annotations

from enum import StrEnum


class ReconciliationJobStatus(StrEnum):
    """Durable lifecycle for reconciliation_jobs rows."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class JobFailureCategory(StrEnum):
    """Minimal failure classification for worker retry decisions."""

    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"


class IncidentSeverity(StrEnum):
    """Operational severity for persisted reconciliation incidents."""

    HIGH = "high"  # Field mismatch (DISCREPANCY)
    MEDIUM = "medium"  # Correlation gap (PROBE_ONLY / WEBHOOK_ONLY)


class AlertChannel(StrEnum):
    """Supported alert delivery channels."""

    WEBHOOK = "webhook"


class AlertDeliveryStatus(StrEnum):
    """Durable alert delivery lifecycle."""

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    RETRYABLE = "retryable"
