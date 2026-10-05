"""Reconciliation discrepancy ORM model."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.alert_delivery import AlertDelivery
    from app.models.merchant import Merchant


class ReconDiscrepancy(Base):
    """Ledger drift detected during reconciliation."""

    __tablename__ = "recon_discrepancies"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    webhook_event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
    )
    stripe_event_id: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="HIGH")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    probe_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    stripe_intent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    merchant: Mapped[Merchant] = relationship(
        "Merchant",
        back_populates="discrepancies",
        lazy="selectin",
    )
    alert_deliveries: Mapped[list[AlertDelivery]] = relationship(
        "AlertDelivery",
        back_populates="discrepancy",
        lazy="selectin",
    )
