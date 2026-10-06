from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

AlertChannelEnum = Enum("slack", "pagerduty", "email", "webhook", name="alert_channel")
AlertStatusEnum = Enum("sent", "failed", name="alert_status")


class AlertDispatch(Base):
    __tablename__ = "alert_dispatches"
    __table_args__ = (
        Index("ix_alert_dispatches_incident_id", "incident_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(AlertChannelEnum, nullable=False)
    destination: Mapped[str] = mapped_column(String(2000), nullable=False)
    status: Mapped[str] = mapped_column(AlertStatusEnum, nullable=False)
    http_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
