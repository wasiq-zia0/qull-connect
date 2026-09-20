"""Statute-citing demand letter generation (PDF)."""
import re
from datetime import date
from .laws import get_state
from .engine import compute_deadline

# Strip control characters from user-supplied text before rendering it into
# the PDF. User input is untrusted data: it is printed verbatim as text
# (never interpreted as markup or instructions).
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize(text) -> str:
    return _CONTROL_CHARS.sub("", str(text or ""))


def letter_body(case: dict, law: dict, deadline_iso: str | None = None, days_overdue: int = 0) -> str:
    """Factual request only; never invents itemization, bad faith or penalty facts."""
    context = (f"The preliminary return/accounting deadline calculated from the dates I supplied is {deadline_iso}. "
               f"The referenced rule is {law['statute']}. Please explain any applicable deductions, estimates or exceptions."
               if deadline_iso else "Please confirm the return/accounting deadline and explain any proposed deductions under the applicable rules.")
    return f"""{sanitize(case['tenant_name'])}
{sanitize(case['tenant_forwarding_address'])}

{date.today().strftime('%B %d, %Y')}

{sanitize(case['landlord_name'])}
{sanitize(case['landlord_address'])}

Re: Request for security deposit return and accounting
Property: {sanitize(case['rental_address'])}
Move-out date provided: {sanitize(case['move_out'])}

Dear {sanitize(case['landlord_name'])},

My recorded security deposit for this property is ${case['deposit']:,.2f}. I request the return of any balance owed to me and a written itemized accounting of any deductions. Please tell me when payment and the accounting will be provided.

{context}

Please send your response and any payment to the forwarding address above. If a payment or accounting has already been sent, please provide its date, amount and delivery details so I can reconcile my records.

Sincerely,
{sanitize(case['tenant_name'])}

Prepared from user-supplied facts. Review every statement before sending. This tool does not determine legal liability, provide representation or send this letter.
Reference: {law.get('official_source_url', '')}
"""


def build_pdf(case: dict, path: str) -> str:
    # ReportLab supports safe literal text and wraps long addresses over pages.
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.pagesizes import letter
    from xml.sax.saxutils import escape
    law = get_state(case["state"])
    dl = compute_deadline(case["state"], date.fromisoformat(case["move_out"]),
                          date.fromisoformat(case["forwarding_date"]) if case.get("forwarding_date") else None,
                          tenancy_end=date.fromisoformat(case["tenancy_end"]) if case.get("tenancy_end") else None)
    body = letter_body(case, law, dl["deadline"])
    styles = getSampleStyleSheet()
    flow = []
    for paragraph in body.split("\n\n"):
        flow += [Paragraph(escape(paragraph).replace("\n", "<br/>"), styles["Normal"]), Spacer(1, 10)]
    SimpleDocTemplate(path, pagesize=letter, leftMargin=60, rightMargin=60).build(flow)
    return path
