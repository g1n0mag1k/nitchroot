"""SQLAlchemy ORM models for durable SnitchRoot state."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.persistence.enums import (
    AlertChannel,
    AlertDeliveryStatus,
    IncidentSeverity,
    JobFailureCategory,
    ReconciliationJobStatus,
)
from app.reconciliation.models import ReconciliationStatus


class Merchant(Base):
    """Merchant account resolved from webhook metadata or provisioning."""

    __tablename__ = "merchants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    external_ref: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    webhook_events: Mapped[list[WebhookEventRow]] = relationship(back_populates="merchant")


class WebhookEventRow(Base):
    """Persisted normalized webhook (no raw Stripe payload)."""

    __tablename__ = "webhook_events"
    __table_args__ = (UniqueConstraint("stripe_event_id", name="uq_webhook_stripe_event_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("merchants.id"), nullable=False, index=True
    )
    stripe_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    livemode: Mapped[bool] = mapped_column(nullable=False, default=False)
    api_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    object_id: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    object_status: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    stripe_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    merchant: Mapped[Merchant] = relationship(back_populates="webhook_events")


class ProbeResultRow(Base):
    """Persisted probe execution used for sliding-window reconciliation."""

    __tablename__ = "probe_results"
    __table_args__ = (Index("ix_probe_merchant_intent", "merchant_id", "stripe_intent_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("merchants.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    stripe_intent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    amount: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    currency: Mapped[str] = mapped_column(String(16), nullable=False, default="usd")
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    probed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReconciliationJob(Base):
    """Durable reconciliation work item created after webhook persistence."""

    __tablename__ = "reconciliation_jobs"
    __table_args__ = (Index("ix_recon_jobs_status_created", "status", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("merchants.id"), nullable=False, index=True
    )
    webhook_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("webhook_events.id"), nullable=False, unique=True
    )
    status: Mapped[ReconciliationJobStatus] = mapped_column(
        String(32), nullable=False, default=ReconciliationJobStatus.PENDING
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    failure_category: Mapped[JobFailureCategory | None] = mapped_column(String(32), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    webhook_event: Mapped[WebhookEventRow] = relationship()
    result: Mapped[ReconciliationResultRow | None] = relationship(
        back_populates="job", uselist=False
    )


class ReconciliationResultRow(Base):
    """Immutable persisted outcome of a completed reconciliation job."""

    __tablename__ = "reconciliation_results"
    __table_args__ = (UniqueConstraint("job_id", name="uq_reconciliation_results_job_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reconciliation_jobs.id"), nullable=False
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("merchants.id"), nullable=False, index=True
    )
    status: Mapped[ReconciliationStatus] = mapped_column(String(32), nullable=False)
    probe_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    stripe_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stripe_intent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    discrepancies: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    probe_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    webhook_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    probe_currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    webhook_currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    reconciled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    job: Mapped[ReconciliationJob] = relationship(back_populates="result")


class ReconDiscrepancy(Base):
    """Durable incident record for non-MATCHED reconciliation outcomes."""

    __tablename__ = "recon_discrepancies"
    __table_args__ = (
        UniqueConstraint("reconciliation_job_id", name="uq_recon_discrepancies_job_id"),
        Index("ix_recon_discrepancies_merchant_detected", "merchant_id", "detected_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("merchants.id"), nullable=False, index=True
    )
    reconciliation_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reconciliation_jobs.id"), nullable=False
    )
    webhook_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("webhook_events.id"), nullable=True
    )
    probe_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    payment_intent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    discrepancy_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[IncidentSeverity] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    dossier_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    dossier_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manifest_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dossier_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    alert_deliveries: Mapped[list[AlertDeliveryRow]] = relationship(back_populates="incident")


class AlertDeliveryRow(Base):
    """Durable alert delivery attempt per incident and channel."""

    __tablename__ = "alert_deliveries"
    __table_args__ = (
        UniqueConstraint("incident_id", "channel", name="uq_alert_deliveries_incident_channel"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recon_discrepancies.id"), nullable=False, index=True
    )
    channel: Mapped[AlertChannel] = mapped_column(String(32), nullable=False)
    status: Mapped[AlertDeliveryStatus] = mapped_column(
        String(32), nullable=False, default=AlertDeliveryStatus.PENDING
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    incident: Mapped[ReconDiscrepancy] = relationship(back_populates="alert_deliveries")
