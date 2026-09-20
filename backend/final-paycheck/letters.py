"""Factual wage-request PDFs, written with wrapping and pagination."""
import os
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import LETTER
from sanitize import clean_text, money

LETTERS_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent / "data")) / "letters"


def generate_demand_letter(case: dict, law: dict) -> Path:
    LETTERS_DIR.mkdir(parents=True, exist_ok=True)
    path = LETTERS_DIR / f"demand_letter_{case['id']}.pdf"
    deadline = (f"The preliminary payment deadline calculated from my information is {law['deadline']}. Please explain any exception you believe applies."
                if law.get("deadline") else "Please confirm the applicable final-pay deadline and provide an itemized calculation of wages and deductions.")
    paragraphs = ["REQUEST FOR UNPAID FINAL WAGES", date.today().isoformat(),
      clean_text(case['employer_name'], 120) + "\n" + clean_text(case.get('employer_address', ''), 500),
      "Dear " + clean_text(case['employer_name'], 120) + ",",
      f"My last day of work was {clean_text(case['last_day_worked'], 30)}. My records show {money(case['wages_owed_cents'])} in unpaid wages for {clean_text(case.get('pay_period') or 'my final pay period', 80)}. Please reconcile this amount with your records and arrange payment of any amount owed.",
      deadline,
      "If payment has already been issued, please provide the amount, date and payment details. Please send your response and any payment to:",
      clean_text(case.get('forwarding_address', ''), 500),
      "Sincerely,\n" + clean_text(case['employee_name'], 120) + "\n" + clean_text(case.get('employee_email', ''), 120),
      "Prepared from user-supplied facts. Review before sending. This is not legal advice or representation, and this tool does not send the request or determine penalties.",
      "Reference: " + law.get('official_source_url', 'https://www.dol.gov/agencies/whd/state/contacts')]
    style = getSampleStyleSheet()['Normal']
    flow = []
    for text in paragraphs:
        flow.extend([Paragraph(escape(text).replace("\n", "<br/>"), style), Spacer(1, 12)])
    SimpleDocTemplate(str(path), pagesize=LETTER, leftMargin=60, rightMargin=60).build(flow)
    return path
