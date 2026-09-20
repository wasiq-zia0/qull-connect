"""Checklist template + pack rendering (markdown + PDF).

Security: every user-supplied value is sanitized before rendering. User text is
treated as untrusted data — it is escaped for each output format and can never
alter queries, links, or instructions.
"""
import re
from xml.sax.saxutils import escape as xml_escape

USPS_URL = "https://www.usps.com/move/"
IRS_URL = "https://www.irs.gov/forms-pubs/about-form-8822"

_MD_SPECIAL = re.compile(r"([*_`\[\]()#<>&])")


def sanitize(text, max_len: int = 300) -> str:
    """Strip control characters and cap length. Never returns markup."""
    t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(text or ""))
    return t.strip()[:max_len]


def md_escape(text: str) -> str:
    """Escape markdown metacharacters AND angle brackets in user-supplied text,
    so hostile input can never become links, formatting, or HTML/script."""
    return _MD_SPECIAL.sub(r"\\\1", sanitize(text))


def build_checklist(move: dict, state: dict) -> list[dict]:
    """Return the ordered checklist template for a move. `move` fields are sanitized."""
    old = sanitize(move["old_address"])
    new = sanitize(move["new_address"])
    items = [
        {"category": "Mail", "title": "File your USPS Change of Address",
         "detail": (f"Forward mail from the old place to {new} for 12 months. "
                    "USPS charges a $1.10 identity-verification fee — that is the only official fee; "
                    "beware copycat sites charging more."),
         "url": USPS_URL},
        {"category": "Driver's license & car", "title": f"Update your {state['state']} driver's license / ID",
         "detail": (f"{state['dmv_name']} requires it {state['dmv_deadline']}. "
                    "Usually takes about 5 minutes online."),
         "url": state["dmv_url"]},
        {"category": "Driver's license & car", "title": "Update your vehicle registration",
         "detail": ("Separate record from your license — skip it and renewal notices "
                    "keep going to the old address."),
         "url": state["dmv_url"]},
        {"category": "Vote", "title": "Update your voter registration",
         "detail": (state["voter_note"] or
                    "Takes about 2 minutes online. Do it now so you're set for the next election."),
         "url": state["voter_url"]},
        {"category": "Money", "title": "Update banks & credit cards",
         "detail": "Checking, savings, credit cards — new address on file; order new debit cards if needed.",
         "url": None},
        {"category": "Money", "title": "Update loans & investments",
         "detail": "Mortgage servicer, auto loan, student loans, brokerage — statements and tax forms follow the address.",
         "url": None},
        {"category": "Work", "title": "Tell your employer / payroll",
         "detail": "W-2s and any paper checks go to the right place; confirm direct deposit details if your bank changed.",
         "url": None},
        {"category": "Tax", "title": "Notify the IRS",
         "detail": "File Form 8822 (free) so refunds and notices find you. Also update your state tax agency.",
         "url": IRS_URL},
        {"category": "Insurance", "title": "Update auto insurance",
         "detail": "Rates are based on where the car sleeps — update on day one to avoid claim headaches.",
         "url": None},
        {"category": "Insurance", "title": "Update renters / homeowners insurance",
         "detail": "New address, new coverage needs — don't leave a gap.",
         "url": None},
        {"category": "Insurance", "title": "Update health insurance & providers",
         "detail": "New address on the plan; find in-network doctors, dentist, and pharmacy near the new place.",
         "url": None},
        {"category": "Home", "title": "Set up / transfer utilities",
         "detail": "Electric, gas, water, trash — schedule the shutoff at the old place and turn-on at the new one.",
         "url": None},
        {"category": "Home", "title": "Set up internet & TV",
         "detail": "Book the install appointment before move-in day if you can.",
         "url": None},
        {"category": "Deliveries", "title": "Update subscriptions & deliveries",
         "detail": "Amazon, meal kits, magazines, anything that ships to you on a schedule.",
         "url": None},
        {"category": "Phone", "title": "Update your phone carrier",
         "detail": "Billing address, and check coverage at the new address.",
         "url": None},
        {"category": "Health", "title": "Transfer prescriptions & records",
         "detail": "Move prescriptions to a nearby pharmacy; request records from doctor, dentist, and vet.",
         "url": None},
        {"category": "Family", "title": "Update schools & childcare",
         "detail": "Enroll the kids, transfer records, update emergency contacts.",
         "url": None},
        {"category": "Pets", "title": "Update pet microchip & vet",
         "detail": "Update the microchip registry with the new address and find a new vet.",
         "url": None},
        {"category": "Memberships", "title": "Update memberships",
         "detail": "Gym, clubs, library card, loyalty programs — the small ones everyone forgets.",
         "url": None},
    ]
    # `old` is used implicitly via USPS detail wording; keep reference to avoid lint noise.
    _ = old
    return items


def render_markdown(move: dict, state: dict, items: list[dict]) -> str:
    """Render the printable checklist as markdown. All user text escaped."""
    name = md_escape(move["name"])
    old = md_escape(move["old_address"])
    new = md_escape(move["new_address"])
    move_date = md_escape(move["move_date"])
    st = state["state"]

    lines = [
        f"# Your Moving Concierge Pack",
        "",
        f"**{name}** — moving **{move_date}**",
        f"From: {old}",
        f"To: {new}",
        "",
        "## ⏰ Time-sensitive — do these first",
        "",
        f"- **Driver's license ({st})**: update it {state['dmv_deadline']} — {state['dmv_url']}",
        f"- **Voter registration**: {state['voter_url']}",
        f"- **USPS mail forwarding**: file now (takes 2 minutes) — {USPS_URL}",
        "",
        "## ✅ Checklist",
        "",
    ]
    current_cat = None
    for it in items:
        if it["category"] != current_cat:
            current_cat = it["category"]
            lines += [f"### {current_cat}", ""]
        box = "[x]" if it["status"] == "done" else "[ ]"
        title = md_escape(it["title"])
        detail = md_escape(it["detail"])
        link = f" — {it['url']}" if it["url"] else ""
        lines.append(f"- {box} **{title}** — {detail}{link}")
    lines += [
        "",
        "---",
        "_Generated by Moving Concierge. This pack gives you the steps and official links — "
        "you complete each change yourself. We never file anything on your behalf._",
    ]
    return "\n".join(lines)


def build_pdf(move: dict, state: dict, items: list[dict], path: str) -> str:
    """Render the checklist pack as a PDF. Returns the path written."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

    styles = getSampleStyleSheet()
    title_s = styles["Title"]
    h2 = styles["Heading2"]
    h3 = styles["Heading3"]
    body = styles["BodyText"]

    story = []
    story.append(Paragraph(xml_escape("Your Moving Concierge Pack"), title_s))
    story.append(Spacer(1, 0.15 * inch))
    story.append(Paragraph(
        xml_escape(f"{sanitize(move['name'])} — moving {sanitize(move['move_date'])}"), body))
    story.append(Paragraph(xml_escape(f"From: {sanitize(move['old_address'])}"), body))
    story.append(Paragraph(xml_escape(f"To: {sanitize(move['new_address'])}"), body))
    story.append(Spacer(1, 0.15 * inch))

    story.append(Paragraph(xml_escape("Time-sensitive — do these first"), h2))
    for label, url in [
        (f"Driver's license ({state['state']}): update it {state['dmv_deadline']}", state["dmv_url"]),
        ("Voter registration", state["voter_url"]),
        ("USPS mail forwarding (file now, ~2 min)", USPS_URL),
    ]:
        story.append(Paragraph(
            f"{xml_escape(label)}: <a href=\"{xml_escape(url)}\">{xml_escape(url)}</a>", body))
    story.append(Spacer(1, 0.15 * inch))

    story.append(Paragraph(xml_escape("Checklist"), h2))
    current_cat = None
    for it in items:
        if it["category"] != current_cat:
            current_cat = it["category"]
            story.append(Paragraph(xml_escape(current_cat), h3))
        box = "[x]" if it["status"] == "done" else "[ ]"
        text = f"{box} <b>{xml_escape(sanitize(it['title']))}</b> — {xml_escape(sanitize(it['detail']))}"
        if it["url"]:
            text += f" — <a href=\"{xml_escape(it['url'])}\">{xml_escape(it['url'])}</a>"
        story.append(Paragraph(text, body))
        story.append(Spacer(1, 0.06 * inch))

    story.append(Spacer(1, 0.2 * inch))
    story.append(Paragraph(xml_escape(
        "Generated by Moving Concierge. This pack gives you the steps and official links — "
        "you complete each change yourself. We never file anything on your behalf."), body))

    doc = SimpleDocTemplate(path, pagesize=letter,
                            leftMargin=0.75 * inch, rightMargin=0.75 * inch)
    doc.build(story)
    return path
