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
