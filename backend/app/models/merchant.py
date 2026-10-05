"""Merchant ORM model."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.recon_discrepancy import ReconDiscrepancy


class Merchant(Base):
    """Stripe merchant monitored by SnitchRoot."""

    __tablename__ = "merchants"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    discrepancies: Mapped[list[ReconDiscrepancy]] = relationship(
        "ReconDiscrepancy",
        back_populates="merchant",
        lazy="selectin",
    )
