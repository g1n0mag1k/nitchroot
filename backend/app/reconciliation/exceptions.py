"""Worker-facing reconciliation job errors."""

from __future__ import annotations


class ReconciliationJobError(Exception):
    """Base class for reconciliation job failures."""

    def __init__(self, message: str, *, category: str) -> None:
        super().__init__(message)
        self.message = message
        self.category = category


class RetryableJobError(ReconciliationJobError):
    """Transient failure — job may be retried."""

    def __init__(self, message: str) -> None:
        super().__init__(message, category="retryable")


class NonRetryableJobError(ReconciliationJobError):
    """Permanent data/logic failure — job must not retry."""

    def __init__(self, message: str) -> None:
        super().__init__(message, category="non_retryable")
