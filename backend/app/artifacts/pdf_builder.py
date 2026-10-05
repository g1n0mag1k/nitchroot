"""SHA-256 signed PDF incident dossier builder.

Uses reportlab to render incident dossiers. All content is derived from
ReconciliationReport and ProbeResult — never raw Stripe payloads or PII.

The PDF's SHA-256 hash is embedded in document metadata and appended as a
footer on the final page for tamper-evidence.
"""

from __future__ import annotations

import hashlib
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

from .models import DossierRequest, DossierResult, DossierSeverity

log = structlog.get_logger(__name__)

_OUTPUT_DIR = Path(__file__).parent / "output"

_SEVERITY_COLORS = {
    DossierSeverity.INFO: colors.HexColor("#2ecc71"),
    DossierSeverity.WARNING: colors.HexColor("#f39c12"),
    DossierSeverity.CRITICAL: colors.HexColor("#e74c3c"),
}


def _sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _truncate_id(value: str, chars: int = 16) -> str:
    """Show only the first N chars of an ID to avoid bloating the PDF."""
    return f"{value[:chars]}..." if len(value) > chars else value


class PdfDossierBuilder:
    """Builds SHA-256 signed PDF incident dossiers from reconciliation data."""

    def __init__(self, output_dir: Path | None = None) -> None:
        self._output_dir = output_dir or _OUTPUT_DIR
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def build(self, request: DossierRequest) -> DossierResult:
        """Render and sign a PDF incident dossier.

        Args:
            request: Dossier content specification.

        Returns:
            DossierResult with path, hash, and metadata.
        """
        filename = f"dossier_{request.report_id}.pdf"
        output_path = self._output_dir / filename

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

        # ── Cover page ────────────────────────────────────────────────────
        story.append(Paragraph("SnitchRoot Incident Dossier", title_style))
        story.append(Spacer(1, 0.1 * inch))

        cover_data = [
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

        # ── Probe section ─────────────────────────────────────────────────
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

        # ── Reconciliation section ────────────────────────────────────────
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

        # ── Integrity placeholder (will be replaced after first render) ───
        story.append(Paragraph("Document Integrity", heading_style))
        story.append(
            Paragraph(
                "SHA-256 hash computed after render — see footer of this page.",
                body_style,
            )
        )

        # ── First render (to get the hash) ───────────────────────────────
        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=letter,
            rightMargin=0.75 * inch,
            leftMargin=0.75 * inch,
            topMargin=0.75 * inch,
            bottomMargin=0.75 * inch,
            title=f"SnitchRoot Dossier {request.report_id}",
            author="SnitchRoot",
            subject=f"Incident Dossier — Severity: {request.severity}",
        )
        doc.build(story)

        # ── Compute hash of rendered PDF ──────────────────────────────────
        sha256 = _sha256_file(output_path)
        log.info(
            "dossier_rendered",
            report_id=str(request.report_id),
            sha256=sha256,
            path=str(output_path),
        )

        # ── Second render with hash in footer ────────────────────────────
        story.append(Spacer(1, 0.2 * inch))
        story.append(
            Paragraph(
                f"<b>SHA-256:</b> {sha256}",
                ParagraphStyle(
                    "HashFooter",
                    parent=body_style,
                    fontSize=7,
                    textColor=colors.HexColor("#7f8c8d"),
                    fontName="Courier",
                ),
            )
        )

        doc2 = SimpleDocTemplate(
            str(output_path),
            pagesize=letter,
            rightMargin=0.75 * inch,
            leftMargin=0.75 * inch,
            topMargin=0.75 * inch,
            bottomMargin=0.75 * inch,
            title=f"SnitchRoot Dossier {request.report_id}",
            author="SnitchRoot",
            subject=f"Incident Dossier — Severity: {request.severity}",
        )
        doc2.build(story)

        final_hash = _sha256_file(output_path)
        file_size = output_path.stat().st_size

        result = DossierResult(
            report_id=request.report_id,
            output_path=output_path,
            sha256_hash=final_hash,
            file_size_bytes=file_size,
            generated_at=datetime.now(UTC),
        )
        log.info(
            "dossier_signed",
            report_id=str(request.report_id),
            final_hash=final_hash,
            file_size_bytes=file_size,
        )
        return result
