"""Initial webhook and reconciliation tables.

Revision ID: 20261005_0001
Revises:
Create Date: 2026-10-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261005_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "merchants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "webhook_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("merchant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stripe_event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payment_intent_id", sa.String(length=255), nullable=True),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=16), nullable=False),
        sa.Column("object_status", sa.String(length=64), nullable=False),
        sa.Column("livemode", sa.Boolean(), nullable=False),
        sa.Column("api_version", sa.String(length=32), nullable=False),
        sa.Column("stripe_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stripe_event_id", name="uq_webhook_events_stripe_event_id"),
    )
    op.create_index("ix_webhook_events_merchant_id", "webhook_events", ["merchant_id"])
    op.create_index("ix_webhook_events_payment_intent_id", "webhook_events", ["payment_intent_id"])
    op.create_table(
        "probe_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("probe_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("merchant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stripe_intent_id", sa.String(length=255), nullable=True),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("latency_ms", sa.BigInteger(), nullable=False),
        sa.Column("raw_response_hash", sa.String(length=64), nullable=True),
        sa.Column("probed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_probe_results_idempotency_key"),
        sa.UniqueConstraint("probe_id"),
    )
    op.create_index("ix_probe_results_merchant_id", "probe_results", ["merchant_id"])
    op.create_index("ix_probe_results_stripe_intent_id", "probe_results", ["stripe_intent_id"])
    op.create_table(
        "reconciliation_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("merchant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("webhook_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stripe_event_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["webhook_event_id"], ["webhook_events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stripe_event_id", name="uq_reconciliation_jobs_stripe_event_id"),
        sa.UniqueConstraint("webhook_event_id", name="uq_reconciliation_jobs_webhook_event_id"),
    )
    op.create_index("ix_reconciliation_jobs_merchant_id", "reconciliation_jobs", ["merchant_id"])
    op.create_table(
        "reconciliation_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reconciliation_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("merchant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("report_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("discrepancies", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("stripe_event_id", sa.String(length=255), nullable=True),
        sa.Column("stripe_intent_id", sa.String(length=255), nullable=True),
        sa.Column("probe_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("probe_amount", sa.Integer(), nullable=True),
        sa.Column("webhook_amount", sa.Integer(), nullable=True),
        sa.Column("probe_currency", sa.String(length=16), nullable=True),
        sa.Column("webhook_currency", sa.String(length=16), nullable=True),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.Column("report_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["reconciliation_job_id"], ["reconciliation_jobs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reconciliation_job_id", name="uq_reconciliation_reports_job_id"),
    )


def downgrade() -> None:
    op.drop_table("reconciliation_reports")
    op.drop_table("reconciliation_jobs")
    op.drop_table("probe_results")
    op.drop_table("webhook_events")
    op.drop_table("merchants")
