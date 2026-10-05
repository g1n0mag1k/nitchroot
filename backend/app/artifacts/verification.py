"""Verify dossier PDF bytes against sidecar manifest."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.artifacts.digest import sha256_file
from app.artifacts.manifest import DossierManifest, read_manifest


@dataclass(frozen=True)
class DossierVerificationResult:
    """Outcome of comparing a PDF file to its manifest."""

    ok: bool
    reason: str
    expected_sha256: str | None = None
    actual_sha256: str | None = None
    manifest: DossierManifest | None = None


def verify_dossier(pdf_path: Path, manifest_path: Path) -> DossierVerificationResult:
    """Compare SHA-256 of PDF bytes to the digest recorded in the manifest."""
    if not pdf_path.is_file():
        return DossierVerificationResult(ok=False, reason="pdf_missing")
    if not manifest_path.is_file():
        return DossierVerificationResult(ok=False, reason="manifest_missing")

    try:
        manifest = read_manifest(manifest_path)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return DossierVerificationResult(ok=False, reason=f"manifest_invalid:{exc}")

    actual = sha256_file(pdf_path)
    expected = manifest.dossier_sha256
    if actual != expected:
        return DossierVerificationResult(
            ok=False,
            reason="hash_mismatch",
            expected_sha256=expected,
            actual_sha256=actual,
            manifest=manifest,
        )

    if manifest.dossier_filename != pdf_path.name:
        return DossierVerificationResult(
            ok=False,
            reason="filename_mismatch",
            expected_sha256=expected,
            actual_sha256=actual,
            manifest=manifest,
        )

    return DossierVerificationResult(
        ok=True,
        reason="ok",
        expected_sha256=expected,
        actual_sha256=actual,
        manifest=manifest,
    )
