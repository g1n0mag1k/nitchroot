"""Merchant ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.api_key import APIKey


class Merchant(Base):
    """A SnitchRoot customer (Stripe account under monitoring)."""

    __tablename__ = "merchants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    stripe_restricted_key_enc: Mapped[str] = mapped_column(String(1024), nullable=False)
    probe_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=900)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    api_keys: Mapped[list[APIKey]] = relationship(
        "APIKey",
        back_populates="merchant",
        lazy="selectin",
    )
