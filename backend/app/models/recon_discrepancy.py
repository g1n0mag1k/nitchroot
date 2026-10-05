"""ReconDiscrepancy ORM model."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.core.database import Base
from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column


class ReconDiscrepancy(Base):
    """Ledger drift or settlement mismatch detected during reconciliation."""

    __tablename__ = "recon_discrepancies"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("merchants.id"),
        nullable=False,
    )
    webhook_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("webhook_events.id"),
        nullable=True,
    )
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    dossier_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    dossier_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
