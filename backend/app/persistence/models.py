"""SQLAlchemy ORM models for SnitchRoot persistence."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base
from app.probes.models import ProbeFailureMode, ProbeStatus
from app.reconciliation.models import ReconciliationStatus


class MerchantOperationalMode(StrEnum):
    """Whether the merchant is probed against live or sandbox Stripe."""

    LIVE = "live"
    SANDBOX = "sandbox"


class AlertChannel(StrEnum):
    """Outbound alert delivery channels (persisted for later dispatch)."""

    SLACK = "slack"
    PAGERDUTY = "pagerduty"
    DATADOG = "datadog"
    WEBHOOK = "webhook"


class Merchant(Base):
    """Merchant configuration and encrypted Stripe credentials."""

    __tablename__ = "merchants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    stripe_restricted_key_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    probe_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=900)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    operational_mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=MerchantOperationalMode.SANDBOX.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    probe_results: Mapped[list[ProbeResultRow]] = relationship(
        back_populates="merchant",
        cascade="all, delete-orphan",
    )
    webhook_events: Mapped[list[WebhookEventRow]] = relationship(
        back_populates="merchant",
        cascade="all, delete-orphan",
    )
    reconciliation_reports: Mapped[list[ReconciliationReportRow]] = relationship(
        back_populates="merchant",
        cascade="all, delete-orphan",
    )


class ProbeResultRow(Base):
    """Persisted probe execution result (no raw Stripe payloads)."""

    __tablename__ = "probe_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        comment="Matches domain ProbeResult.probe_id",
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    failure_mode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stripe_intent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_response_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    probed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="usd")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    merchant: Mapped[Merchant] = relationship(back_populates="probe_results")

    __table_args__ = (Index("ix_probe_results_merchant_probed_at", "merchant_id", "probed_at"),)


class WebhookEventRow(Base):
    """Normalized Stripe webhook event — never stores raw payload."""

    __tablename__ = "webhook_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="Matches NormalizedWebhookEvent.internal_id",
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stripe_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    livemode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    api_version: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    object_id: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    object_status: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="")
    event_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sanitized_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    merchant: Mapped[Merchant] = relationship(back_populates="webhook_events")
    reconciliation_reports: Mapped[list[ReconciliationReportRow]] = relationship(
        back_populates="webhook_event",
    )

    __table_args__ = (
        UniqueConstraint("stripe_event_id", name="uq_webhook_events_stripe_event_id"),
        Index("ix_webhook_events_merchant_received_at", "merchant_id", "received_at"),
    )


class ReconciliationReportRow(Base):
    """Persisted reconciliation output linking probe and webhook evidence."""

    __tablename__ = "reconciliation_reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="Matches ReconciliationReport.report_id",
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    probe_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("probe_results.id", ondelete="SET NULL"),
        nullable=True,
    )
    webhook_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("webhook_events.id", ondelete="SET NULL"),
        nullable=True,
    )
    stripe_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stripe_intent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ReconciliationStatus.PROBE_ONLY.value,
    )
    discrepancies: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)),
        nullable=False,
        default=list,
    )
    probe_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    webhook_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    probe_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    webhook_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    reconciled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dossier_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    dossier_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    merchant: Mapped[Merchant] = relationship(back_populates="reconciliation_reports")
    webhook_event: Mapped[WebhookEventRow | None] = relationship(
        back_populates="reconciliation_reports",
    )
    alert_deliveries: Mapped[list[AlertDelivery]] = relationship(
        back_populates="reconciliation_report",
        cascade="all, delete-orphan",
    )


class AlertDelivery(Base):
    """Alert delivery state for a reconciliation report (dispatch in a later phase)."""

    __tablename__ = "alert_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    reconciliation_report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reconciliation_reports.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    reconciliation_report: Mapped[ReconciliationReportRow] = relationship(
        back_populates="alert_deliveries",
    )


# Re-export domain enums used when writing rows (for type checkers in mappers).
__all__ = [
    "AlertChannel",
    "AlertDelivery",
    "Merchant",
    "MerchantOperationalMode",
    "ProbeFailureMode",
    "ProbeResultRow",
    "ProbeStatus",
    "ReconciliationReportRow",
    "ReconciliationStatus",
    "WebhookEventRow",
]
