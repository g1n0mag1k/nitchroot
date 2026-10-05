"""SQLAlchemy persistence layer for SnitchRoot domain entities."""

from app.persistence.encryption import MerchantKeyEncryption, MerchantKeyEncryptionError
from app.persistence.mappers import (
    merchant_to_orm,
    orm_to_probe_result,
    orm_to_reconciliation_report,
    probe_result_to_orm,
    reconciliation_report_to_orm,
    webhook_event_to_orm,
)
from app.persistence.models import (
    AlertChannel,
    AlertDelivery,
    Merchant,
    MerchantOperationalMode,
    ProbeResultRow,
    ReconciliationReportRow,
    WebhookEventRow,
)

__all__ = [
    "AlertChannel",
    "AlertDelivery",
    "Merchant",
    "MerchantKeyEncryption",
    "MerchantKeyEncryptionError",
    "MerchantOperationalMode",
    "ProbeResultRow",
    "ReconciliationReportRow",
    "WebhookEventRow",
    "merchant_to_orm",
    "orm_to_probe_result",
    "orm_to_reconciliation_report",
    "probe_result_to_orm",
    "reconciliation_report_to_orm",
    "webhook_event_to_orm",
]
