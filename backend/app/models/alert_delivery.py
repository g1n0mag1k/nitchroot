"""AlertDelivery ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

from app.core.database import Base
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column


class AlertDelivery(Base):
    """Delivery attempt for a reconciliation discrepancy alert."""

    __tablename__ = "alert_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    discrepancy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recon_discrepancies.id"),
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
