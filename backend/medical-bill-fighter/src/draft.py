"""Conversational intake: the golden path is trigger -> one tap -> done.

Instead of a heavy form, the agent asks one question at a time. Each answer
returns a `user_message` — a warm, speakable sentence the agent can say
verbatim — plus the next question. When the last question is answered, the
draft finalizes: detection runs and the payoff message is returned.

Every response is fully demoable inside a plain chat transcript.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

from .models import CaseIntake

# (field, question asked in user_message)
_STEPS: list[tuple[str, str]] = [
    ("patient_name", "What's your full name, as it appears on the bill?"),
    ("provider_name", "Got it. Which hospital or provider sent this bill?"),
    ("bill_date", "And what's the date on the bill? (any format works, like 2026-08-15)"),
    ("total", "How much are they asking you to pay? Just the number is fine."),
    ("line_items", ("If you have the itemized detail, paste the line items — one per line like "
                    "'99213 180'. Or just say 'skip' and I'll work with the total.")),
    ("eob", ("Do you have your insurance EOB handy? Tell me the patient-responsibility "
             "amount it shows, or say 'skip'.")),
    ("emergency", "Quick one: was this emergency care? (yes/no)"),
    ("out_of_network", "And was the provider out-of-network? (yes/no)"),
]


def _parse_money(text: str) -> Optional[float]:
    m = re.search(r"\$?\s*([\d,]+(?:\.\d{1,2})?)", text.replace(" ", ""))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _parse_date(text: str) -> Optional[str]:
    text = text.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%B %d, %Y", "%b %d, %Y", "%m/%d/%y"):
        try:
            from datetime import datetime
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_yes_no(text: str) -> Optional[bool]:
    t = text.strip().lower()
    if t in ("yes", "y", "yeah", "yep", "true", "1"):
        return True
    if t in ("no", "n", "nope", "false", "0"):
        return False
    return None


def _parse_line_items(text: str) -> list[dict[str, Any]]:
    """Parse pasted lines like '99213 Office visit 180' or '99213 180'."""
    items: list[dict[str, Any]] = []
    for raw in text.strip().splitlines():
        line = raw.strip()
        if not line:
            continue
        # code is first token; amount is the last number on the line
        code = line.split()[0].upper()
        amounts = re.findall(r"\$?([\d,]+(?:\.\d{1,2})?)", line)
        if not amounts:
            continue
        amount = float(amounts[-1].replace(",", ""))
        middle = line[len(code):].strip()
        desc = middle[: len(middle) - len(amounts[-1])].strip(" -$") or code
        items.append({"code": code, "description": desc[:300], "amount": amount, "quantity": 1})
    return items


def new_draft(prefill: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Create a draft state dict, pre-filling any fields the trigger payload provides."""
    prefill = prefill or {}
    answers: dict[str, Any] = {}
    for key in ("patient_name", "provider_name", "bill_date", "total", "insurance_plan"):
        if prefill.get(key) is not None:
            answers[key] = prefill[key]
    step = 0
    field = _STEPS[step][0]
    while field in answers:
        step += 1
        field = _STEPS[step][0]
    return {"status": "draft", "step": step, "answers": answers}


def _question_for(step: int) -> str:
    return _STEPS[step][1]


def apply_answer(draft: dict[str, Any], answer_text: str) -> dict[str, Any]:
    """Advance the draft with the user's answer.

    Returns {"user_message": ..., "done": bool, "question": ..., "case": ... (when done)}.
    Validation failures keep the same step and re-ask with a gentle nudge.
    """
    answers = draft["answers"]
    step = draft["step"]
    field, question = _STEPS[step]
    text = (answer_text or "").strip()

    def reask(hint: str) -> dict[str, Any]:
        return {"done": False, "question": question,
                "user_message": f"{hint} {question}"}

    if field == "patient_name":
        if len(text) < 2:
            return reask("I need a name to put on the letters.")
        answers[field] = text[:120]
    elif field == "provider_name":
        if len(text) < 2:
            return reask("Which provider sent the bill?")
        answers[field] = text[:200]
    elif field == "bill_date":
        parsed = _parse_date(text)
        if not parsed:
            return reask("I didn't catch that date — try something like 2026-08-15.")
        answers[field] = parsed
    elif field == "total":
        amount = _parse_money(text)
        if amount is None or amount <= 0:
            return reask("How much is the bill for? Just a number like 4200.")
        answers[field] = amount
    elif field == "line_items":
        if text.lower() in ("skip", "no", "none", "n/a"):
            answers[field] = []
        else:
            items = _parse_line_items(text)
            if not items:
                return reask("I couldn't read those lines — try one per line like '99213 180', or say 'skip'.")
            answers[field] = items
    elif field == "eob":
        if text.lower() in ("skip", "no", "none", "n/a", "don't have one", "dont have one"):
            answers[field] = None
        else:
            amount = _parse_money(text)
            if amount is None:
                return reask("What's the patient-responsibility amount on your EOB? Or say 'skip'.")
            answers[field] = amount
    elif field in ("emergency", "out_of_network"):
        val = _parse_yes_no(text)
        if val is None:
            return reask("Just yes or no works.")
        answers[field] = val

    step += 1
    draft["step"] = step
    draft["answers"] = answers

    if step >= len(_STEPS):
        return {"done": True, "case": finalize(draft)}
    return {"done": False, "question": _question_for(step),
            "user_message": _question_for(step)}


def finalize(draft: dict[str, Any]) -> CaseIntake:
    """Turn completed draft answers into a validated CaseIntake."""
    a = draft["answers"]
    line_items = a.get("line_items") or []
    if not line_items:
        # No detail: single summary line so the detector can still run the
        # missing-itemization rule and total-based rules.
        line_items = [{
            "code": "SUMMARY",
            "description": f"Bill total from {a.get('provider_name', 'provider')} (no itemized detail provided)",
            "amount": float(a["total"]),
            "quantity": 1,
        }]
    eob_amount = a.get("eob")
    return CaseIntake(
        patient_name=a["patient_name"],
        provider_name=a["provider_name"],
        bill_date=date.fromisoformat(a["bill_date"]),
        billed_patient_responsibility=float(a["total"]),
        line_items=line_items,
        eob=({"allowed_amount": float(a["total"]),
              "patient_responsibility": float(eob_amount),
              "deductible_applied": 0.0, "coinsurance": 0.0}
             if eob_amount is not None else None),
        is_emergency=bool(a.get("emergency")),
        is_out_of_network=bool(a.get("out_of_network")),
    )
