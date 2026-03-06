import os
import logging
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from models.load import Load

logger = logging.getLogger(__name__)

# Use /tmp on Vercel (only writable path); falls back to ./pdfs locally
PDF_OUTPUT_DIR = os.environ.get("PDF_OUTPUT_DIR", "/tmp/pdfs")

_BRAND_COLOR = colors.HexColor("#1a3a5c")
_ACCENT_COLOR = colors.HexColor("#e8700a")


class PDFGenerationError(Exception):
    pass


def _ensure_output_dir() -> None:
    os.makedirs(PDF_OUTPUT_DIR, exist_ok=True)


def _build_pdf_filename(load_id: str, doc_type: str) -> str:
    return os.path.join(PDF_OUTPUT_DIR, f"{load_id}_{doc_type}.pdf")


def _get_styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        "DocTitle",
        parent=styles["Heading1"],
        fontSize=20,
        textColor=_BRAND_COLOR,
        spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        "SectionHeading",
        parent=styles["Heading2"],
        fontSize=11,
        textColor=_BRAND_COLOR,
        spaceBefore=12,
        spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        "FieldLabel",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.HexColor("#666666"),
    ))
    styles.add(ParagraphStyle(
        "FieldValue",
        parent=styles["Normal"],
        fontSize=10,
        spaceBefore=1,
    ))
    styles.add(ParagraphStyle(
        "Footer",
        parent=styles["Normal"],
        fontSize=7,
        textColor=colors.grey,
        alignment=1,  # center
    ))
    return styles


def _signature_block(styles) -> list:
    elements = []
    elements.append(Spacer(1, 0.3 * inch))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.lightgrey))
    elements.append(Spacer(1, 0.15 * inch))

    sig_data = [
        ["Authorized By:", "", "Date:"],
        ["", "", ""],
        ["_" * 35, "", "_" * 20],
        ["Signature", "", ""],
    ]
    sig_table = Table(sig_data, colWidths=[3 * inch, 1 * inch, 2.5 * inch])
    sig_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#666666")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
    ]))
    elements.append(sig_table)
    return elements


def generate_rate_confirmation(load: Load) -> str:
    """Generate Rate Confirmation PDF. Returns absolute file path."""
    _ensure_output_dir()
    path = _build_pdf_filename(load.load_id, "rate_con")
    styles = _get_styles()

    try:
        doc = SimpleDocTemplate(
            path,
            pagesize=letter,
            rightMargin=0.75 * inch,
            leftMargin=0.75 * inch,
            topMargin=0.75 * inch,
            bottomMargin=0.75 * inch,
        )
        elements = []

        # Header
        elements.append(Paragraph("RATE CONFIRMATION", styles["DocTitle"]))
        generated_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        elements.append(Paragraph(
            f"Load ID: <b>{load.load_id}</b> &nbsp;&nbsp;|&nbsp;&nbsp; Generated: {generated_ts}",
            styles["Normal"],
        ))
        elements.append(HRFlowable(width="100%", thickness=2, color=_BRAND_COLOR))
        elements.append(Spacer(1, 0.1 * inch))

        # Broker / Parties
        elements.append(Paragraph("BROKER / CARRIER", styles["SectionHeading"]))
        party_data = [
            ["Broker Name:", load.logistics.broker_name],
            ["Broker Email:", load.broker_email],
            ["Shipper:", "Pipe Prince Operations"],
        ]
        party_table = Table(party_data, colWidths=[1.5 * inch, 5 * inch])
        party_table.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#666666")),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        elements.append(party_table)

        # Cargo
        elements.append(Paragraph("CARGO", styles["SectionHeading"]))
        cargo_data = [
            ["Type:", load.cargo.type.replace("_", " ").title()],
            ["Quantity:", f"{load.cargo.quantity} {load.cargo.unit}"],
            ["Specifications:", load.cargo.specs],
        ]
        cargo_table = Table(cargo_data, colWidths=[1.5 * inch, 5 * inch])
        cargo_table.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#666666")),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        elements.append(cargo_table)

        # Route
        elements.append(Paragraph("ROUTE", styles["SectionHeading"]))
        route_data = [
            ["Origin:", load.route.origin],
            ["Destination:", load.route.destination],
            ["Total Miles:", f"{load.route.total_miles} miles"],
        ]
        route_table = Table(route_data, colWidths=[1.5 * inch, 5 * inch])
        route_table.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#666666")),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        elements.append(route_table)

        # Rate
        elements.append(Paragraph("RATE", styles["SectionHeading"]))
        cpm_str = f"${load.cost_per_mile:.4f}/mile" if load.cost_per_mile else "N/A"
        rate_data = [
            ["Agreed Rate:", f"${load.logistics.quoted_rate:,.2f} {load.logistics.currency}"],
            ["Cost Per Mile:", cpm_str],
            ["ETA:", load.logistics.eta.strftime("%Y-%m-%d %H:%M UTC")],
        ]
        rate_table = Table(rate_data, colWidths=[1.5 * inch, 5 * inch])
        rate_table.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#666666")),
            ("FONTNAME", (1, 0), (1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (1, 0), (1, 0), 12),
            ("TEXTCOLOR", (1, 0), (1, 0), _ACCENT_COLOR),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        elements.append(rate_table)

        # Signature block
        elements.extend(_signature_block(styles))

        # Footer
        elements.append(Spacer(1, 0.3 * inch))
        elements.append(Paragraph(
            "Generated by Pipe-Stream Logistics Engine — This document constitutes a binding rate agreement.",
            styles["Footer"],
        ))

        doc.build(elements)
        logger.info("Generated rate confirmation: %s", path)
        return os.path.abspath(path)

    except Exception as exc:
        raise PDFGenerationError(f"Failed to generate rate confirmation: {exc}") from exc


def generate_release_document(load: Load) -> str:
    """Generate Release Document PDF. Returns absolute file path."""
    _ensure_output_dir()
    path = _build_pdf_filename(load.load_id, "release_doc")
    styles = _get_styles()

    try:
        doc = SimpleDocTemplate(
            path,
            pagesize=letter,
            rightMargin=0.75 * inch,
            leftMargin=0.75 * inch,
            topMargin=0.75 * inch,
            bottomMargin=0.75 * inch,
        )
        elements = []

        # Header
        elements.append(Paragraph("RELEASE DOCUMENT", styles["DocTitle"]))
        generated_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        elements.append(Paragraph(
            f"Load ID: <b>{load.load_id}</b> &nbsp;&nbsp;|&nbsp;&nbsp; Generated: {generated_ts}",
            styles["Normal"],
        ))
        elements.append(HRFlowable(width="100%", thickness=2, color=_BRAND_COLOR))
        elements.append(Spacer(1, 0.1 * inch))

        # Authorization statement
        elements.append(Paragraph("RELEASE AUTHORIZATION", styles["SectionHeading"]))
        stmt = (
            f"This document authorizes the release of the cargo described below from the "
            f"origin yard at <b>{load.route.origin}</b> to the carrier/broker "
            f"<b>{load.logistics.broker_name}</b> for delivery to "
            f"<b>{load.route.destination}</b>."
        )
        elements.append(Paragraph(stmt, styles["Normal"]))

        # Origin yard details
        elements.append(Paragraph("ORIGIN YARD", styles["SectionHeading"]))
        origin_data = [
            ["Location:", load.route.origin],
            ["Authorized By:", "Pipe Prince Operations"],
            ["Release Date:", load.logistics.eta.strftime("%Y-%m-%d")],
        ]
        origin_table = Table(origin_data, colWidths=[1.5 * inch, 5 * inch])
        origin_table.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#666666")),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        elements.append(origin_table)

        # Cargo manifest
        elements.append(Paragraph("CARGO MANIFEST", styles["SectionHeading"]))
        manifest_header = [
            Paragraph("<b>Type</b>", styles["Normal"]),
            Paragraph("<b>Quantity</b>", styles["Normal"]),
            Paragraph("<b>Unit</b>", styles["Normal"]),
            Paragraph("<b>Specifications</b>", styles["Normal"]),
        ]
        manifest_row = [
            load.cargo.type.replace("_", " ").title(),
            str(load.cargo.quantity),
            load.cargo.unit,
            load.cargo.specs,
        ]
        manifest_table = Table(
            [manifest_header, manifest_row],
            colWidths=[1.5 * inch, 1 * inch, 0.75 * inch, 3.25 * inch],
        )
        manifest_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), _BRAND_COLOR),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ]))
        elements.append(manifest_table)

        # Yard manager instructions
        elements.append(Paragraph("INSTRUCTIONS FOR YARD MANAGER", styles["SectionHeading"]))
        instructions = [
            "1. Verify driver ID and truck registration before releasing cargo.",
            f"2. Count and confirm: <b>{load.cargo.quantity} {load.cargo.unit}</b> of {load.cargo.specs}.",
            "3. Note any discrepancies on this document before signing.",
            "4. Retain a copy of this release document for your records.",
            f"5. Confirm release via email to {load.broker_email} and operations@pipeprince.com.",
        ]
        for instr in instructions:
            elements.append(Paragraph(instr, styles["Normal"]))
            elements.append(Spacer(1, 0.05 * inch))

        # Signature block
        elements.extend(_signature_block(styles))

        # Footer
        elements.append(Spacer(1, 0.3 * inch))
        elements.append(Paragraph(
            f"Generated by Pipe-Stream Logistics Engine — Load {load.load_id} — "
            "Yard Manager: retain this document for 90 days.",
            styles["Footer"],
        ))

        doc.build(elements)
        logger.info("Generated release document: %s", path)
        return os.path.abspath(path)

    except Exception as exc:
        raise PDFGenerationError(f"Failed to generate release document: {exc}") from exc
