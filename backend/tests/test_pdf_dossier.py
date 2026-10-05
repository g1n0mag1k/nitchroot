"""Tests for the PDF dossier builder.

Uses a tmp_path fixture so no PDFs are written to the real output directory.
No Stripe, Redis, or DB calls.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.artifacts.models import DossierRequest, DossierSeverity
from app.artifacts.pdf_builder import PdfDossierBuilder, _sha256_file, _truncate_id


# ── helper ───────────────────────────────────────────────────────────────────

def _make_request(
    severity: DossierSeverity = DossierSeverity.INFO,
    discrepancies: list[str] | None = None,
    webhook_amount: int | None = None,
) -> DossierRequest:
    return DossierRequest(
        merchant_id=uuid.uuid4(),
        severity=severity,
        probe_id=str(uuid.uuid4()),
        probe_status="succeeded",
        probe_duration_ms=42.5,
        probe_failure_mode="",
        probe_amount=100,
        probe_currency="usd",
        reconciliation_status="matched",
        discrepancies=discrepancies or [],
        webhook_amount=webhook_amount,
        webhook_currency="usd" if webhook_amount is not None else None,
    )


# ── unit tests ────────────────────────────────────────────────────────────────

class TestTruncateId:
    def test_long_id_is_truncated(self) -> None:
        val = "a" * 32
        result = _truncate_id(val, 16)
        assert result == "a" * 16 + "..."

    def test_short_id_unchanged(self) -> None:
        val = "short"
        assert _truncate_id(val, 16) == "short"

    def test_exact_length_unchanged(self) -> None:
        val = "a" * 16
        assert _truncate_id(val, 16) == val


class TestSha256File:
    def test_deterministic(self, tmp_path: Path) -> None:
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello snitchroot")
        h1 = _sha256_file(f)
        h2 = _sha256_file(f)
        assert h1 == h2

    def test_known_value(self, tmp_path: Path) -> None:
        f = tmp_path / "known.bin"
        f.write_bytes(b"")
        expected = hashlib.sha256(b"").hexdigest()
        assert _sha256_file(f) == expected

    def test_different_content_different_hash(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"aaa")
        f2.write_bytes(b"bbb")
        assert _sha256_file(f1) != _sha256_file(f2)


class TestPdfDossierBuilder:
    def test_builds_pdf_file(self, tmp_path: Path) -> None:
        builder = PdfDossierBuilder(output_dir=tmp_path)
        req = _make_request()
        result = builder.build(req)
        assert result.output_path.exists()
        assert result.output_path.suffix == ".pdf"

    def test_result_report_id_matches_request(self, tmp_path: Path) -> None:
        builder = PdfDossierBuilder(output_dir=tmp_path)
        req = _make_request()
        result = builder.build(req)
        assert result.report_id == req.report_id

    def test_sha256_is_hex_string(self, tmp_path: Path) -> None:
        builder = PdfDossierBuilder(output_dir=tmp_path)
        result = builder.build(_make_request())
        assert len(result.sha256_hash) == 64
        assert all(c in "0123456789abcdef" for c in result.sha256_hash)

    def test_file_size_nonzero(self, tmp_path: Path) -> None:
        builder = PdfDossierBuilder(output_dir=tmp_path)
        result = builder.build(_make_request())
        assert result.file_size_bytes > 0

    def test_critical_severity_builds(self, tmp_path: Path) -> None:
        builder = PdfDossierBuilder(output_dir=tmp_path)
        req = _make_request(
            severity=DossierSeverity.CRITICAL,
            discrepancies=["amount", "currency"],
            webhook_amount=999,
        )
        result = builder.build(req)
        assert result.output_path.exists()

    def test_warning_severity_builds(self, tmp_path: Path) -> None:
        builder = PdfDossierBuilder(output_dir=tmp_path)
        req = _make_request(severity=DossierSeverity.WARNING)
        result = builder.build(req)
        assert result.output_path.exists()

    def test_different_reports_have_different_hashes(self, tmp_path: Path) -> None:
        builder = PdfDossierBuilder(output_dir=tmp_path)
        r1 = builder.build(_make_request())
        r2 = builder.build(_make_request())  # different report_id → different content
        assert r1.sha256_hash != r2.sha256_hash

    def test_output_dir_created_if_missing(self, tmp_path: Path) -> None:
        nested = tmp_path / "deep" / "nested" / "output"
        builder = PdfDossierBuilder(output_dir=nested)
        result = builder.build(_make_request())
        assert nested.exists()
        assert result.output_path.exists()

    def test_dossier_result_is_frozen(self, tmp_path: Path) -> None:
        builder = PdfDossierBuilder(output_dir=tmp_path)
        result = builder.build(_make_request())
        with pytest.raises((AttributeError, TypeError)):
            result.sha256_hash = "tampered"  # type: ignore[misc]

    def test_generated_at_is_utc(self, tmp_path: Path) -> None:
        builder = PdfDossierBuilder(output_dir=tmp_path)
        result = builder.build(_make_request())
        assert result.generated_at.tzinfo is not None
