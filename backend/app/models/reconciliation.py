"""Reconciliation job and report persistence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ReconciliationJob(Base):
    """Work item to reconcile one webhook event against probe records.

    Reconciliation execution is deferred to a Redis-backed worker; the API
    only enqueues jobs after webhook persistence.
    """

    __tablename__ = "reconciliation_jobs"
    __table_args__ = (
        UniqueConstraint("webhook_event_id", name="uq_reconciliation_jobs_webhook_event_id"),
        UniqueConstraint("stripe_event_id", name="uq_reconciliation_jobs_stripe_event_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    webhook_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("webhook_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    stripe_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReconciliationReportRecord(Base):
    """Immutable output of ReconciliationEngine for a completed job."""

    __tablename__ = "reconciliation_reports"
    __table_args__ = (
        UniqueConstraint("reconciliation_job_id", name="uq_reconciliation_reports_job_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    reconciliation_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reconciliation_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    report_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    discrepancies: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    stripe_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stripe_intent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    probe_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    probe_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    webhook_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    probe_currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    webhook_currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    report_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
