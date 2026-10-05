"""Initial persistence schema for merchants, probes, webhooks, and reconciliation.

Revision ID: 20261005_0001
Revises:
Create Date: 2026-10-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261005_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "merchants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("stripe_restricted_key_enc", sa.LargeBinary(), nullable=False),
        sa.Column("probe_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("operational_mode", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "probe_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("merchant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("failure_mode", sa.String(length=64), nullable=True),
        sa.Column("stripe_intent_id", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("raw_response_hash", sa.String(length=64), nullable=True),
        sa.Column("probed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_probe_results_merchant_id", "probe_results", ["merchant_id"])
    op.create_index(
        "ix_probe_results_merchant_probed_at",
        "probe_results",
        ["merchant_id", "probed_at"],
    )
    op.create_table(
        "webhook_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("merchant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stripe_event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("livemode", sa.Boolean(), nullable=False),
        sa.Column("api_version", sa.String(length=32), nullable=False),
        sa.Column("object_id", sa.String(length=255), nullable=False),
        sa.Column("object_status", sa.String(length=64), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("event_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "sanitized_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stripe_event_id", name="uq_webhook_events_stripe_event_id"),
    )
    op.create_index("ix_webhook_events_merchant_id", "webhook_events", ["merchant_id"])
    op.create_index(
        "ix_webhook_events_merchant_received_at",
        "webhook_events",
        ["merchant_id", "received_at"],
    )
    op.create_table(
        "reconciliation_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("merchant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("probe_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("webhook_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("stripe_event_id", sa.String(length=255), nullable=True),
        sa.Column("stripe_intent_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("discrepancies", postgresql.ARRAY(sa.String(length=32)), nullable=False),
        sa.Column("probe_amount", sa.Integer(), nullable=True),
        sa.Column("webhook_amount", sa.Integer(), nullable=True),
        sa.Column("probe_currency", sa.String(length=3), nullable=True),
        sa.Column("webhook_currency", sa.String(length=3), nullable=True),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dossier_path", sa.String(length=1024), nullable=True),
        sa.Column("dossier_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["probe_id"], ["probe_results.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["webhook_event_id"],
            ["webhook_events.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_reconciliation_reports_merchant_id",
        "reconciliation_reports",
        ["merchant_id"],
    )
    op.create_table(
        "alert_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reconciliation_report_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["reconciliation_report_id"],
            ["reconciliation_reports.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_alert_deliveries_reconciliation_report_id",
        "alert_deliveries",
        ["reconciliation_report_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_alert_deliveries_reconciliation_report_id", table_name="alert_deliveries")
    op.drop_table("alert_deliveries")
    op.drop_index("ix_reconciliation_reports_merchant_id", table_name="reconciliation_reports")
    op.drop_table("reconciliation_reports")
    op.drop_index("ix_webhook_events_merchant_received_at", table_name="webhook_events")
    op.drop_index("ix_webhook_events_merchant_id", table_name="webhook_events")
    op.drop_table("webhook_events")
    op.drop_index("ix_probe_results_merchant_probed_at", table_name="probe_results")
    op.drop_index("ix_probe_results_merchant_id", table_name="probe_results")
    op.drop_table("probe_results")
    op.drop_table("merchants")
