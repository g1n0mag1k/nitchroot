"""Merchant ORM model."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.core.database import Base
from sqlalchemy import DateTime, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column


class Merchant(Base):
    """A monitored merchant with encrypted Stripe restricted key material."""

    __tablename__ = "merchants"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    stripe_restricted_key_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    probe_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=900)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
