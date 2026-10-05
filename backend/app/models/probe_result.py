"""ProbeResult ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

from app.core.database import Base
from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column


class ProbeResult(Base):
    """Outcome of a zero-fee Stripe PaymentIntent probe for a merchant."""

    __tablename__ = "probe_results"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("merchants.id"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    failure_mode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stripe_intent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_response_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    probed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
