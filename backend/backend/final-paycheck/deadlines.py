"""Deadline engine for final-paycheck cases.

Computes each case's legal deadline from the encoded state rule and the
termination type. Rule shapes are documented in data/final_pay_laws.json.

Conservative by design: ambiguous rules resolve to the interpretation that
avoids a premature demand letter. `FINAL_PAYCHECK_TODAY=YYYY-MM-DD` overrides
the clock for deterministic testing.
"""
import json
import os
from datetime import date, timedelta
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent / "data" / "final_pay_laws.json"

_laws: dict | None = None


def load_laws() -> dict:
    global _laws
    if _laws is None:
        _laws = json.loads(DATA_PATH.read_text())
    return _laws


def get_state(abbr: str) -> dict | None:
    abbr = abbr.upper()
    for s in load_laws()["states"]:
        if s["abbr"] == abbr:
            return s
    return None


def today() -> date:
    override = os.environ.get("FINAL_PAYCHECK_TODAY")
    if override:
        return date.fromisoformat(override)
    return date.today()


def _add_working_days(start: date, n: int) -> date:
    d = start
    added = 0
    while added < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def _apply_rule(rule: dict, last_day: date, next_payday: date | None) -> tuple[date | None, str | None]:
    """Return (deadline, missing_info). missing_info is set when a rule
    needs next_payday but none was provided."""
    kind = rule.get("kind")
    if kind == "immediate":
        return last_day, None
    if kind == "calendar_days":
        return last_day + timedelta(days=rule["days"]), None
    if kind == "working_days":
        return _add_working_days(last_day, rule["days"]), None
    if kind == "next_payday":
        if next_payday is None:
            return None, "next_payday"
        deadline = next_payday
        if rule.get("min_days_after_last_day"):
            floor = last_day + timedelta(days=rule["min_days_after_last_day"])
            deadline = max(deadline, floor)
        if rule.get("max_days_after_last_day"):
            cap = last_day + timedelta(days=rule["max_days_after_last_day"])
            deadline = min(deadline, cap)
        return deadline, None
    if kind == "none":
        return None, None
    if kind in ("earlier_of", "later_of"):
        results = []
        missing = []
        for opt in rule["options"]:
            dl, miss = _apply_rule(opt, last_day, next_payday)
            if miss:
                missing.append(miss)
            elif dl is not None:
                results.append(dl)
        if missing and not results:
            return None, missing[0]
        if not results:
            return None, None
        chosen = min(results) if kind == "earlier_of" else max(results)
        if rule.get("cap_days"):
            chosen = min(chosen, last_day + timedelta(days=rule["cap_days"]))
        return chosen, None
    return None, None


def compute_deadline(abbr: str, termination_type: str, last_day: date,
                     next_payday: date | None = None) -> dict:
    """Compute deadline/status for a case. Returns a dict with deadline
    (ISO date or None), status, rule summary, and any flags."""
    state = get_state(abbr)
    if state is None:
        return {"error": f"unknown state abbreviation: {abbr}"}
    kind = "fired" if termination_type in ("fired", "laid_off") else "quit"
    rule = state[kind]
    deadline, missing = _apply_rule(rule, last_day, next_payday)

    flags: list[str] = []
    if state.get("notes") and "FLAG" in state["notes"]:
        flags.append("rule_flagged_for_lawyer_review")

    if rule.get("kind") == "none":
        return {
            "state": abbr.upper(), "state_name": state["name"],
            "termination_type": kind, "rule": rule,
            "statute": state.get("statute", ""), "penalty_note": state.get("penalty_note", ""),
            "deadline": None, "status": "no_state_deadline",
            "days_overdue": 0, "days_remaining": None,
            "missing": None, "flags": flags,
            "note": (f"{state['name']} sets no specific final-paycheck deadline. "
                     "This case cannot be auto-escalated; consult an employment lawyer."),
        }
    if missing == "next_payday":
        return {
            "state": abbr.upper(), "state_name": state["name"],
            "termination_type": kind, "rule": rule,
            "statute": state.get("statute", ""), "penalty_note": state.get("penalty_note", ""),
            "deadline": None, "status": "needs_info",
            "days_overdue": 0, "days_remaining": None,
            "missing": "next_payday",
            "flags": flags,
            "note": "State rule depends on the next regular payday; provide next_payday to compute the deadline.",
        }

    now = today()
    assert deadline is not None
    overdue = now > deadline
    return {
        "state": abbr.upper(), "state_name": state["name"],
        "termination_type": kind, "rule": rule,
        "statute": state.get("statute", ""), "penalty_note": state.get("penalty_note", ""),
        "deadline": deadline.isoformat(),
        "status": "overdue" if overdue else "waiting",
        "days_overdue": (now - deadline).days if overdue else 0,
        "days_remaining": (deadline - now).days if not overdue else 0,
        "missing": None, "flags": flags, "note": "",
    }
