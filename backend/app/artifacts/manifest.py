"""Sidecar JSON manifest for dossier integrity verification."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.artifacts.digest import sha256_bytes, sha256_file

MANIFEST_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class DossierManifest:
    """Structured manifest referencing the final PDF bytes."""

    schema_version: int
    incident_id: uuid.UUID
    merchant_id: uuid.UUID
    dossier_filename: str
    dossier_sha256: str
    generated_at: datetime
    application_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "incident_id": str(self.incident_id),
            "merchant_id": str(self.merchant_id),
            "dossier_filename": self.dossier_filename,
            "dossier_sha256": self.dossier_sha256,
            "generated_at": self.generated_at.isoformat(),
            "application_version": self.application_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DossierManifest:
        return cls(
            schema_version=int(data["schema_version"]),
            incident_id=uuid.UUID(str(data["incident_id"])),
            merchant_id=uuid.UUID(str(data["merchant_id"])),
            dossier_filename=str(data["dossier_filename"]),
            dossier_sha256=str(data["dossier_sha256"]),
            generated_at=datetime.fromisoformat(str(data["generated_at"])),
            application_version=str(data["application_version"]),
        )


def write_manifest(manifest: DossierManifest, path: Path) -> str:
    """Serialize manifest to disk and return SHA-256 of the manifest file bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(manifest.to_dict(), indent=2, sort_keys=True).encode("utf-8")
    path.write_bytes(payload)
    return sha256_file(path)


def read_manifest(path: Path) -> DossierManifest:
    """Load and parse a manifest file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manifest root must be a JSON object")
    return DossierManifest.from_dict(data)


def manifest_content_hash(manifest: DossierManifest) -> str:
    """Deterministic SHA-256 of canonical manifest JSON (without writing a file)."""
    payload = json.dumps(manifest.to_dict(), indent=2, sort_keys=True).encode("utf-8")
    return sha256_bytes(payload)
