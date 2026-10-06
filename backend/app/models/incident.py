from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

IncidentSeverityEnum = Enum("info", "warning", "critical", name="incident_severity")


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        Index("ix_incidents_merchant_resolved_at", "merchant_id", "resolved_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    reconciliation_report_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("reconciliation_reports.id", ondelete="SET NULL"), nullable=True
    )
    probe_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("probe_runs.id", ondelete="SET NULL"), nullable=True
    )
    severity: Mapped[str] = mapped_column(IncidentSeverityEnum, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
