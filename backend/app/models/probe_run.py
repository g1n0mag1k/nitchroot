from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

ProbeStatusEnum = Enum("succeeded", "failed", name="probe_status")
ProbeFailureModeEnum = Enum(
    "auth_error", "creation_error", "schema_drift", "cancel_error",
    name="probe_failure_mode",
)


class ProbeRun(Base):
    __tablename__ = "probe_runs"
    __table_args__ = (
        Index("ix_probe_runs_merchant_probed_at", "merchant_id", "probed_at"),
        Index("ix_probe_runs_probed_at", "probed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(ProbeStatusEnum, nullable=False)
    failure_mode: Mapped[str | None] = mapped_column(ProbeFailureModeEnum, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    stripe_intent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_response_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    probed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
