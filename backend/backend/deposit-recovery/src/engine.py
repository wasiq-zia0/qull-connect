"""Deadline computation and case status engine."""
from datetime import date, timedelta
from .laws import get_state, requires_forwarding_date


def compute_deadline(state_abbr: str, move_out: date, forwarding_date: date | None = None) -> dict:
    """Return the legal return deadline for a deposit.

    move_out: date the tenant vacated / tenancy terminated.
    forwarding_date: date the tenant gave the landlord a forwarding address.

    In forwarding-dependent states (TX, CT, MN, WY) the legal clock runs from
    the forwarding address date, so a missing forwarding_date raises
    ValueError instead of falling back to move_out. States whose clock runs
    from move-out still anchor on move_out.
    """
    law = get_state(state_abbr)
    abbr = state_abbr.strip().upper()
    days = law["deadline_days"]
    basis = law["deadline_basis"]

    if days is None:
        return {
            "state": law["state"],
            "statute": law["statute"],
            "deadline": None,
            "basis_explanation": law["deadline_note"],
            "note": "This state sets no fixed return deadline; follow the notification procedure in the statute.",
        }

    if requires_forwarding_date(abbr):
        if not forwarding_date:
            raise ValueError(
                f"forwarding_date is required in {abbr}: the legal deadline runs "
                "from the date you provided your forwarding address."
            )
        if basis == "forwarding_address":
            anchor = forwarding_date
            explanation = (
                f"{days} days after you provided a forwarding address "
                f"({anchor.isoformat()})."
            )
        else:  # later_of_move_out_or_forwarding
            anchor = max(move_out, forwarding_date)
            explanation = (
                f"Later of move-out ({move_out.isoformat()}) and receipt of "
                f"forwarding address ({forwarding_date.isoformat()})."
            )
    else:
        anchor = move_out
        explanation = f"{days} days after move-out ({move_out.isoformat()})."

    return {
        "state": law["state"],
        "statute": law["statute"],
        "deadline": (anchor + timedelta(days=days)).isoformat(),
        "basis_explanation": explanation,
        "statute_note": law["deadline_note"],
    }


def case_status(state_abbr: str, move_out: date, deposit: float,
                forwarding_date: date | None = None, today: date | None = None) -> dict:
    """Full case picture: deadline, days remaining/overdue, max recovery."""
    today = today or date.today()
    law = get_state(state_abbr)
    dl = compute_deadline(state_abbr, move_out, forwarding_date)

    result = {
        "state": law["state"],
        "statute": law["statute"],
        "deposit": round(deposit, 2),
        "move_out": move_out.isoformat(),
        "deadline": dl["deadline"],
        "deadline_days": law["deadline_days"],
        "basis_explanation": dl["basis_explanation"],
        "statute_note": law.get("deadline_note"),
        "penalty_multiple": law["penalty_multiple"],
        "penalty_note": law["penalty_note"],
    }

    if dl["deadline"] is None:
        result.update(status="no_fixed_deadline",
                      headline="No fixed statutory deadline in this state",
                      detail=dl["note"])
        return result

    deadline = date.fromisoformat(dl["deadline"])
    delta = (deadline - today).days
    result["days_remaining"] = delta

    mult = law["penalty_multiple"] or 1
    result["max_recovery"] = round(deposit * mult, 2)
    result["our_fee_25pct"] = round(deposit * mult * 0.25, 2)

    if delta >= 0:
        result.update(
            status="waiting",
            headline=f"Landlord has {delta} day(s) left (deadline {deadline.isoformat()})",
            detail="No action yet. We diary the deadline and prepare the demand letter so it goes out on day one past due.",
            next_action="wait",
        )
    else:
        overdue = -delta
        result.update(
            status="overdue",
            headline=f"Deadline passed {overdue} day(s) ago ({deadline.isoformat()})",
            detail=(
                f"Demand letter should go out now citing {law['statute']}. "
                + (f"If bad faith is found, recovery could reach {mult}x the deposit = "
                   f"${result['max_recovery']:,.2f}." if law["penalty_multiple"]
                   else "Additional statutory damages may apply under state law.")
            ),
            next_action="send_demand_letter",
        )
    return result
