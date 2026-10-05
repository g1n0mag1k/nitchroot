"""PDF incident dossier builder.

Uses reportlab to render incident dossiers. All content is derived from
sanitized reconciliation fields — never raw Stripe payloads or secrets.

Integrity: the final PDF is rendered once. A sidecar JSON manifest records
the SHA-256 digest of those bytes (see ``manifest.py``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import structlog
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Flowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.artifacts.digest import sha256_file
from app.artifacts.manifest import (
    MANIFEST_SCHEMA_VERSION,
    DossierManifest,
    write_manifest,
)
from app.core.config import get_settings

from .models import DossierRequest, DossierResult, DossierSeverity

log = structlog.get_logger(__name__)

_OUTPUT_DIR = Path(__file__).parent / "output"

_SEVERITY_COLORS = {
    DossierSeverity.INFO: colors.HexColor("#2ecc71"),
    DossierSeverity.WARNING: colors.HexColor("#f39c12"),
    DossierSeverity.CRITICAL: colors.HexColor("#e74c3c"),
}


def _truncate_id(value: str, chars: int = 16) -> str:
    """Show only the first N chars of an ID to avoid bloating the PDF."""
    return f"{value[:chars]}..." if len(value) > chars else value


def _safe_artifact_basename(incident_id: uuid.UUID) -> str:
    """Filesystem-safe basename for dossier/manifest files."""
    return f"incident_{incident_id}"


class PdfDossierBuilder:
    """Builds PDF incident dossiers and companion integrity manifests."""

    def __init__(self, output_dir: Path | None = None) -> None:
        self._output_dir = output_dir or _OUTPUT_DIR
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def build(self, request: DossierRequest) -> DossierResult:
        """Render a single-pass PDF and write a sidecar manifest.

        Args:
            request: Dossier content specification.

        Returns:
            DossierResult with paths, hashes, and metadata.
        """
        incident_id = request.incident_id or request.report_id
        basename = _safe_artifact_basename(incident_id)
        pdf_filename = f"{basename}.pdf"
        manifest_filename = f"{basename}.manifest.json"
        output_path = self._resolve_output_path(pdf_filename)
        manifest_path = self._resolve_output_path(manifest_filename)

        styles = getSampleStyleSheet()
        severity_color = _SEVERITY_COLORS[request.severity]

        title_style = ParagraphStyle(
            "SnitchRootTitle",
            parent=styles["Title"],
            textColor=severity_color,
            fontSize=20,
            spaceAfter=12,
        )
        heading_style = ParagraphStyle(
            "SnitchRootHeading",
            parent=styles["Heading2"],
            textColor=colors.HexColor("#2c3e50"),
            fontSize=13,
            spaceBefore=16,
            spaceAfter=6,
        )
        body_style = styles["BodyText"]

        story: list[Flowable] = []

        story.append(Paragraph("SnitchRoot Incident Dossier", title_style))
        story.append(Spacer(1, 0.1 * inch))

        cover_data = [
            ["Incident ID", _truncate_id(str(incident_id))],
            ["Report ID", _truncate_id(str(request.report_id))],
            ["Merchant ID", _truncate_id(str(request.merchant_id))],
            ["Severity", request.severity.upper()],
            ["Generated", request.generated_at.strftime("%Y-%m-%d %H:%M:%S UTC")],
        ]
        cover_table = Table(cover_data, colWidths=[2 * inch, 4 * inch])
        cover_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#ecf0f1")),
                    ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    (
                        "ROWBACKGROUNDS",
                        (0, 0),
                        (-1, -1),
                        [colors.white, colors.HexColor("#f8f9fa")],
                    ),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bdc3c7")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(cover_table)
        story.append(Spacer(1, 0.3 * inch))

        story.append(Paragraph("Probe Observation", heading_style))
        probe_data = [
            ["Probe ID (truncated)", _truncate_id(request.probe_id)],
            ["Status", request.probe_status],
            ["Duration (ms)", f"{request.probe_duration_ms:.2f}"],
            ["Failure Mode", request.probe_failure_mode or "—"],
            ["Amount (cents)", str(request.probe_amount)],
            ["Currency", request.probe_currency.upper() if request.probe_currency else "—"],
        ]
        probe_table = Table(probe_data, colWidths=[2 * inch, 4 * inch])
        probe_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#ecf0f1")),
                    ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    (
                        "ROWBACKGROUNDS",
                        (0, 0),
                        (-1, -1),
                        [colors.white, colors.HexColor("#f8f9fa")],
                    ),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bdc3c7")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(probe_table)
        story.append(Spacer(1, 0.3 * inch))

        story.append(Paragraph("Reconciliation Result", heading_style))
        discrepancy_str = ", ".join(request.discrepancies) if request.discrepancies else "None"
        recon_data = [
            ["Status", request.reconciliation_status],
            ["Discrepancies", discrepancy_str],
            ["Probe Amount (cents)", str(request.probe_amount)],
            [
                "Webhook Amount (cents)",
                str(request.webhook_amount) if request.webhook_amount is not None else "—",
            ],
            [
                "Probe Currency",
                request.probe_currency.upper() if request.probe_currency else "—",
            ],
            [
                "Webhook Currency",
                request.webhook_currency.upper() if request.webhook_currency else "—",
            ],
        ]
        recon_table = Table(recon_data, colWidths=[2 * inch, 4 * inch])
        recon_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#ecf0f1")),
                    ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    (
                        "ROWBACKGROUNDS",
                        (0, 0),
                        (-1, -1),
                        [colors.white, colors.HexColor("#f8f9fa")],
                    ),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bdc3c7")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(recon_table)
        story.append(Spacer(1, 0.5 * inch))

        story.append(Paragraph("Document Integrity", heading_style))
        story.append(
            Paragraph(
                (
                    "Content digest is recorded in the companion manifest "
                    f"<b>{manifest_filename}</b> (incident {_truncate_id(str(incident_id))}). "
                    "The manifest SHA-256 matches the final PDF bytes."
                ),
                body_style,
            )
        )

        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=letter,
            rightMargin=0.75 * inch,
            leftMargin=0.75 * inch,
            topMargin=0.75 * inch,
            bottomMargin=0.75 * inch,
            title=f"SnitchRoot Dossier {incident_id}",
            author="SnitchRoot",
            subject=f"Incident Dossier — Severity: {request.severity}",
        )
        doc.build(story)

        dossier_sha256 = sha256_file(output_path)
        generated_at = datetime.now(UTC)
        settings = get_settings()

        manifest = DossierManifest(
            schema_version=MANIFEST_SCHEMA_VERSION,
            incident_id=incident_id,
            merchant_id=request.merchant_id,
            dossier_filename=pdf_filename,
            dossier_sha256=dossier_sha256,
            generated_at=generated_at,
            application_version=settings.app_version,
        )
        manifest_sha256 = write_manifest(manifest, manifest_path)
        file_size = output_path.stat().st_size

        log.info(
            "dossier_built",
            incident_id=str(incident_id),
            dossier_sha256=dossier_sha256,
            manifest_sha256=manifest_sha256,
            path=str(output_path),
        )

        return DossierResult(
            report_id=request.report_id,
            incident_id=incident_id,
            output_path=output_path,
            manifest_path=manifest_path,
            sha256_hash=dossier_sha256,
            manifest_sha256=manifest_sha256,
            file_size_bytes=file_size,
            generated_at=generated_at,
        )

    def _resolve_output_path(self, filename: str) -> Path:
        """Resolve filename under output_dir, rejecting path traversal."""
        if "/" in filename or "\\" in filename or filename.startswith("."):
            raise ValueError("invalid artifact filename")
        resolved = (self._output_dir / filename).resolve()
        base = self._output_dir.resolve()
        if base not in resolved.parents and resolved != base:
            raise ValueError("artifact path escapes output directory")
        return resolved
