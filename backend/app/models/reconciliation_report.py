from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

ReconciliationStatusEnum = Enum(
    "matched", "discrepancy", "probe_only", "webhook_only",
    name="reconciliation_status",
)


class ReconciliationReport(Base):
    __tablename__ = "reconciliation_reports"
    __table_args__ = (
        Index("ix_reconciliation_reports_merchant_created", "merchant_id", "created_at"),
        Index("ix_reconciliation_reports_probe_run_id", "probe_run_id"),
        Index("ix_reconciliation_reports_webhook_event_id", "webhook_event_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("merchants.id", ondelete="SET NULL"), nullable=True
    )
    probe_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("probe_runs.id", ondelete="SET NULL"), nullable=True
    )
    webhook_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("webhook_events.id", ondelete="SET NULL"), nullable=True
    )
    stripe_intent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(ReconciliationStatusEnum, nullable=False)
    discrepancies: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    probe_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    webhook_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    probe_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    webhook_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
