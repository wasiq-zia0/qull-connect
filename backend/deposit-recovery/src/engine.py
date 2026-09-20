"""Limited, source-reviewed deadline calculation; factual requests elsewhere."""
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from .laws import get_state


def compute_deadline(state_abbr: str, move_out: date, forwarding_date: date | None = None, tenancy_end: date | None = None) -> dict:
    law = get_state(state_abbr)
    abbr = law["abbr"]
    result = {"state": law["state"], "statute": law["statute"], "deadline": None,
              "official_source_url": law["official_source_url"],
              "basis_explanation": law["deadline_note"], "verified": False}
    if not law.get("deadline_verified"):
        result["note"] = "This jurisdiction's deadline needs review. A factual return request can be prepared without claiming a missed legal deadline."
        return result
    if abbr == "CT":
        if forwarding_date is None or tenancy_end is None:
            result["note"] = "Connecticut requires the tenancy-end date and the date the landlord received written notice of your forwarding address."
            return result
        deadline = max(tenancy_end + timedelta(days=21), forwarding_date + timedelta(days=15))
    elif abbr == "TX":
        if forwarding_date is None or forwarding_date > move_out + timedelta(days=30):
            result["note"] = "The forwarding-address timing requires review under Texas Property Code 92.107; no new 30-day period is assumed."
            return result
        deadline = move_out + timedelta(days=30)
    else:  # CA: ordinary residential tenancy after the tenant vacates.
        deadline = move_out + timedelta(days=21)
    result.update(deadline=deadline.isoformat(), verified=True,
                  note="Preliminary timing calculation for an ordinary residential tenancy. Deductions, estimates, exceptions and proof still need review.")
    return result


def case_status(state_abbr: str, move_out: date, deposit: float,
                forwarding_date: date | None = None, today: date | None = None, tenancy_end: date | None = None) -> dict:
    today = today or date.today()
    law = get_state(state_abbr)
    dl = compute_deadline(state_abbr, move_out, forwarding_date, tenancy_end)
    result = {"state": law["state"], "statute": law["statute"], "deposit": round(deposit, 2),
              "move_out": move_out.isoformat(), "deadline": dl["deadline"],
              "deadline_days": law["deadline_days"] if dl["verified"] else None,
              "basis_explanation": dl["basis_explanation"], "statute_note": dl.get("note"),
              "official_source_url": dl["official_source_url"], "deadline_verified": dl["verified"],
              "penalty_multiple": None, "penalty_note": "No penalty entitlement or amount has been determined.",
              "estimated_fee_if_full_deposit_recovered_cents": int((Decimal(str(deposit)) * Decimal("25")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))}
    if not dl["verified"]:
        return {**result, "status": "needs_review", "headline": "Legal deadline needs review", "detail": dl.get("note"), "next_action": "prepare_factual_request"}
    delta = (date.fromisoformat(dl["deadline"]) - today).days
    return {**result, "days_remaining": delta, "status": "waiting" if delta >= 0 else "overdue",
            "headline": f"Preliminary return/accounting deadline: {dl['deadline']}",
            "detail": dl["note"], "next_action": "check_case_later" if delta >= 0 else "prepare_return_request"}
