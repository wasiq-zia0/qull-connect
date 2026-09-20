"""Letter-pack PDF generation (reportlab).

SECURITY: every user-supplied string is passed through html.escape() before it
is placed into a reportlab Paragraph. ReportLab Paragraphs interpret a small
XML/HTML markup subset, so unescaped input could alter letter formatting or
inject markup. User data is rendered as inert text only — it never changes
letter structure, logic, or instructions.

Every page carries the footer: "Template automation — NOT legal advice. Review before sending."
"""
from __future__ import annotations

import html
from datetime import date
from io import BytesIO
from typing import Any

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from .models import LEGAL_NOTICE, PACK_DESCRIPTIONS, PackType

PAGE_W, PAGE_H = LETTER


def _esc(value: Any) -> str:
    """Escape user-supplied text for safe inclusion in a Paragraph."""
    return html.escape("" if value is None else str(value), quote=True)


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    normal = ParagraphStyle("mbn_normal", parent=base["Normal"], fontSize=11, leading=15)
    title = ParagraphStyle("mbn_title", parent=base["Heading1"], fontSize=16, leading=20,
                           spaceAfter=12)
    footer = ParagraphStyle("mbn_footer", parent=base["Normal"], fontSize=8, leading=10,
                            textColor=(0.45, 0.45, 0.45), spaceBefore=24)
    return {"normal": normal, "title": title, "footer": footer}


def _doc(title: str) -> tuple[SimpleDocTemplate, BytesIO, dict[str, ParagraphStyle]]:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER,
                            leftMargin=inch, rightMargin=inch,
                            topMargin=inch, bottomMargin=inch,
                            title=title)
    return doc, buf, _styles()


def _footer(story: list, styles: dict[str, ParagraphStyle]) -> None:
    story.append(Paragraph(f"<i>{_esc(LEGAL_NOTICE)}</i>", styles["footer"]))


def _header_block(story: list, styles: dict[str, ParagraphStyle], case: dict[str, Any]) -> None:
    intake = case["intake"]
    today = date.today().isoformat()
    story.append(Paragraph(f"Date: {_esc(today)}", styles["normal"]))
    story.append(Paragraph(f"From: {_esc(intake.get('patient_name'))}", styles["normal"]))
    story.append(Paragraph(f"Provider: {_esc(intake.get('provider_name'))}", styles["normal"]))
    story.append(Paragraph(f"Bill date: {_esc(intake.get('bill_date'))}", styles["normal"]))
    story.append(Spacer(1, 0.25 * inch))


def _findings_section(story: list, styles: dict[str, ParagraphStyle],
                      case: dict[str, Any]) -> None:
    findings = case.get("findings") or []
    relevant = [f for f in findings if f["rule"] in
                ("duplicate_line_items", "bill_vs_eob_mismatch",
                 "possible_unbundling", "missing_itemized_detail")]
    if not relevant:
        story.append(Paragraph(
            "I have reviewed my bill and believe one or more charges may be incorrect.",
            styles["normal"]))
        story.append(Spacer(1, 0.15 * inch))
        return
    story.append(Paragraph("Findings from my review:", styles["normal"]))
    for f in relevant:
        story.append(Paragraph(
            f"<b>{_esc(f['rule'])}</b> ({_esc(f['severity'])}): "
            f"{_esc(f['explanation'])}", styles["normal"]))
        story.append(Spacer(1, 0.1 * inch))


def build_dispute(case: dict[str, Any]) -> bytes:
    doc, buf, styles = _doc("Billing Error Dispute Letter")
    story: list = [Paragraph("Billing Error Dispute Letter", styles["title"])]
    _header_block(story, styles, case)
    story.append(Paragraph(
        f"Dear {_esc(case['intake'].get('provider_name'))} Billing Department,",
        styles["normal"]))
    story.append(Spacer(1, 0.15 * inch))
    story.append(Paragraph(
        "I am writing to dispute charges on my medical bill. I request that you "
        "investigate the items below and correct any errors:", styles["normal"]))
    story.append(Spacer(1, 0.15 * inch))
    _findings_section(story, styles, case)
    story.append(Paragraph(
        "Please provide a corrected, itemized statement and confirm in writing "
        "which charges have been adjusted. I expect a response within 30 days. "
        "I am not refusing to pay amounts I legitimately owe; I am asking that "
        "errors be corrected first.", styles["normal"]))
    story.append(Spacer(1, 0.25 * inch))
    story.append(Paragraph("Sincerely,", styles["normal"]))
    story.append(Paragraph(_esc(case["intake"].get("patient_name")), styles["normal"]))
    _footer(story, styles)
    doc.build(story)
    return buf.getvalue()


def build_itemized(case: dict[str, Any]) -> bytes:
    doc, buf, styles = _doc("Itemized Bill Request")
    story: list = [Paragraph("Request for Itemized Bill", styles["title"])]
    _header_block(story, styles, case)
    story.append(Paragraph(
        f"Dear {_esc(case['intake'].get('provider_name'))} Billing Department,",
        styles["normal"]))
    story.append(Spacer(1, 0.15 * inch))
    story.append(Paragraph(
        "Please send me a fully itemized bill for the services referenced above, "
        "including for each charge: the date of service, the CPT/HCPCS procedure "
        "code, a description, the quantity, and the amount billed.", styles["normal"]))
    story.append(Spacer(1, 0.15 * inch))
    story.append(Paragraph(
        "I am requesting this detail so I can verify the accuracy of my charges "
        "before making payment.", styles["normal"]))
    story.append(Spacer(1, 0.25 * inch))
    story.append(Paragraph("Sincerely,", styles["normal"]))
    story.append(Paragraph(_esc(case["intake"].get("patient_name")), styles["normal"]))
    _footer(story, styles)
    doc.build(story)
    return buf.getvalue()


def build_assistance(case: dict[str, Any]) -> bytes:
    doc, buf, styles = _doc("Financial Assistance Request")
    story: list = [Paragraph("Financial Assistance / Charity Care Request", styles["title"])]
    _header_block(story, styles, case)
    story.append(Paragraph(
        f"Dear {_esc(case['intake'].get('provider_name'))} Financial Counselor,",
        styles["normal"]))
    story.append(Spacer(1, 0.15 * inch))
    story.append(Paragraph(
        "I am writing to request information about financial assistance, charity "
        "care, or payment-plan options for my bill. Please send me your "
        "financial-assistance application and policy, including eligibility "
        "criteria and deadlines.", styles["normal"]))
    story.append(Spacer(1, 0.15 * inch))
    story.append(Paragraph(
        "While my application is under review, I ask that collection activity on "
        "this account be paused.", styles["normal"]))
    story.append(Spacer(1, 0.25 * inch))
    story.append(Paragraph("Sincerely,", styles["normal"]))
    story.append(Paragraph(_esc(case["intake"].get("patient_name")), styles["normal"]))
    _footer(story, styles)
    doc.build(story)
    return buf.getvalue()


def build_negotiate(case: dict[str, Any]) -> bytes:
    intake = case["intake"]
    total = sum(i["amount"] * i.get("quantity", 1) for i in intake.get("line_items", []))
    doc, buf, styles = _doc("Prompt-Pay Negotiation Script")
    story: list = [Paragraph("Prompt-Pay Discount Negotiation Script", styles["title"])]
    _header_block(story, styles, case)
    script_lines = [
        "Hello, I'm calling about my bill of "
        f"${total:,.2f} dated {_esc(intake.get('bill_date'))}.",
        "I'd like to settle this account today. Do you offer a discount for "
        "immediate payment in full?",
        "(If they offer less than 20%: I appreciate that. I've seen that many "
        "providers offer 20% for prompt payment — is there any way you can match "
        "that?)",
        "Can you confirm the discounted amount and send written confirmation "
        "that the account will be marked paid in full?",
        "(If no discount: Do you offer interest-free payment plans? I'd like to "
        "set one up today.)",
    ]
    for line in script_lines:
        story.append(Paragraph(f"• {_esc(line) if not line.startswith('(') else html.escape(line)}",
                               styles["normal"]))
        story.append(Spacer(1, 0.08 * inch))
    story.append(Paragraph(
        "Tip: get any agreement in writing before paying, and never give payment "
        "details over the phone until the discounted balance is confirmed.",
        styles["normal"]))
    _footer(story, styles)
    doc.build(story)
    return buf.getvalue()


_BUILDERS = {
    "dispute": build_dispute,
    "itemized": build_itemized,
    "assistance": build_assistance,
    "negotiate": build_negotiate,
}


def build_pack(case: dict[str, Any], pack_type: PackType) -> bytes:
    """Render the requested letter pack as PDF bytes."""
    if pack_type not in _BUILDERS:
        raise ValueError(f"unknown pack type: {pack_type}")
    return _BUILDERS[pack_type](case)


def describe_packs() -> dict[str, str]:
    return dict(PACK_DESCRIPTIONS)
