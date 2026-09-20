"""MCP server for the final-paycheck connector (mcp SDK v2, streamable-http).

Agent-native: every tool result carries a `user_message` — a warm,
ready-to-speak sentence the agent can say verbatim.
"""
import os
from datetime import date

from mcp.server.mcpserver import MCPServer

import billing
import db
import deadlines
import letters
from identity import IdentityMiddleware, require_owner
from sanitize import money

server = MCPServer(name="final-paycheck")

db.init_db()


def create_mcp_app():
    from identity import IdentityMiddleware
    app = server.streamable_http_app(streamable_http_path="/mcp")
    app.add_middleware(IdentityMiddleware)
    return app


def _owned_case(case_id: str, owner: str):
    """(case, None) for the owner's case — claiming an ownerless life-event
    draft for them — or (None, error_dict) which reads as 404 to anyone else."""
    case = db.get_case(case_id, owner)
    if case is not None:
        return case, None
    claimed = db.claim_case(case_id, owner)
    if claimed is not None:
        return claimed, None
    return None, {"error": f"case not found: {case_id}",
                  "user_message": "I can't find that case — want to start a fresh one?"}

FEE_DISCLOSURE = ("You will be charged 25% of the recovered wages, only if you "
                  "confirm the recovery. No charge otherwise. You can cancel at "
                  "any time before a charge.")


def _law(case: dict) -> dict:
    return deadlines.compute_deadline(
        case["state"], case["termination_type"] or "fired",
        date.fromisoformat(case["last_day_worked"]) if case.get("last_day_worked") else deadlines.today(),
        date.fromisoformat(case["next_payday"]) if case.get("next_payday") else None,
    )


def _status_message(case: dict, law: dict) -> str:
    wages = money(case["wages_owed_cents"])
    employer = case["employer_name"] or "your former employer"
    state_name = law.get("state_name") or case["state"]
    status = law.get("status")
    if status == "overdue":
        d = law["days_overdue"]
        return (f"Your {wages} final paycheck from {employer} is {d} day{'s' if d != 1 else ''} "
                f"past due under {state_name} law. I can draft your demand letter now — one tap.")
    if status == "waiting":
        d = law["days_remaining"]
        return (f"Watching your {wages} from {employer}: {state_name} law says it's due "
                f"in {d} day{'s' if d != 1 else ''}. I'll nudge you the moment it's overdue.")
    if status == "needs_info":
        return (f"To pin down your {state_name} deadline I need your next regular payday date — "
                f"what was it?")
    if status == "no_state_deadline":
        return (f"{state_name} doesn't set a specific final-paycheck deadline, so I can't "
                f"auto-escalate this one — but an employment lawyer still might help.")
    return f"Your case for {wages} from {employer} is being tracked."


@server.tool()
def get_state_law(state: str) -> dict:
    """Look up a state's final-paycheck deadline rule, statute citation, and penalty note."""
    owner = require_owner()  # noqa: F841 - identity enforced for all tenant tools
    s = deadlines.get_state(state)
    if s is None:
        return {"error": f"unknown state abbreviation: {state.upper()}",
                "user_message": "I don't recognize that state — which two-letter state code?"}
    return {**s, "user_message":
            f"In {s['name']}, final pay after being fired vs. quitting follows different "
            f"deadlines — I've got the details."}


@server.tool()
def create_case(employee_name: str, state: str, last_day_worked: str,
                termination_type: str, wages_owed: float, employer_name: str,
                employee_email: str = "", employer_address: str = "",
                pay_period: str = "", next_payday: str | None = None,
                forwarding_address: str = "") -> dict:
    """Open a final-paycheck recovery case. termination_type is fired, laid_off, or quit.
    last_day_worked and next_payday are YYYY-MM-DD dates."""
    abbr = state.upper()
    if deadlines.get_state(abbr) is None:
        return {"error": f"unknown state abbreviation: {abbr}",
                "user_message": "I don't recognize that state — which two-letter state code?"}
    tt = termination_type.lower()
    if tt not in ("fired", "laid_off", "quit"):
        return {"error": "termination_type must be fired, laid_off, or quit",
                "user_message": "Were you fired, laid off, or did you quit? That decides your deadline."}
    owner = require_owner()
    law = deadlines.compute_deadline(abbr, tt, date.fromisoformat(last_day_worked),
                                     date.fromisoformat(next_payday) if next_payday else None)
    if "error" in law:
        return {**law, "user_message": "Something didn't look right — mind checking those details?"}
    case = db.create_case({
        "employee_name": employee_name.strip(), "employee_email": employee_email.strip(),
        "employer_name": employer_name.strip(), "employer_address": employer_address.strip(),
        "state": abbr, "last_day_worked": last_day_worked, "termination_type": tt,
        "wages_owed_cents": int(round(wages_owed * 100)), "pay_period": pay_period.strip(),
        "next_payday": next_payday, "forwarding_address": forwarding_address.strip(),
        "deadline": law.get("deadline"), "status": law.get("status"),
    }, owner_id=owner)
    wages = money(case["wages_owed_cents"])
    return {"case_id": case["id"], "deadline": law.get("deadline"),
            "status": law.get("status"), "statute": law.get("statute"),
            "penalty_note": law.get("penalty_note"), "note": law.get("note"),
            "user_message": _status_message(case, law)}


@server.tool()
def confirm_case(case_id: str) -> dict:
    """One-tap activation of a draft case created from a job_change life event."""
    owner = require_owner()
    case, err = _owned_case(case_id, owner)
    if err:
        return err
    case = db.update_case(case_id, {"is_draft": 0}, owner)
    law = _law(case)
    db.update_case(case_id, {"deadline": law.get("deadline"), "status": law.get("status")}, owner)
    case = db.get_case(case_id, owner)
    return {"case_id": case_id, "deadline": law.get("deadline"), "status": law.get("status"),
            "user_message": f"You're in — I'm now tracking this case. {_status_message(case, law)}"}


@server.tool()
def get_case_status(case_id: str) -> dict:
    """Current status: deadline, days remaining/overdue, statute, billing state."""
    owner = require_owner()
    case, err = _owned_case(case_id, owner)
    if err:
        return err
    law = _law(case)
    return {
        "case_id": case_id, "employee_name": case["employee_name"],
        "employer_name": case["employer_name"], "state": case["state"],
        "deadline": law.get("deadline"), "status": law.get("status"),
        "days_overdue": law.get("days_overdue"), "days_remaining": law.get("days_remaining"),
        "statute": law.get("statute"), "penalty_note": law.get("penalty_note"),
        "wages_owed": case["wages_owed_cents"] / 100,
        "billing_set_up": case.get("stripe_customer_id") is not None,
        "fee_status": case.get("fee_status"),
        "demand_letter_generated": case.get("demand_letter_path") is not None,
        "note": law.get("note"),
        "user_message": _status_message(case, law),
    }


@server.tool()
def generate_demand_letter(case_id: str) -> dict:
    """Generate the statute-citing demand letter PDF. Only allowed once the
    deadline has passed; returns an error otherwise. The PDF is generated for
    the user to send themselves — never sent by this service."""
    owner = require_owner()
    case, err = _owned_case(case_id, owner)
    if err:
        return err
    law = _law(case)
    if law.get("status") != "overdue":
        return {"error": f"case is not overdue (status={law.get('status')})",
                "user_message": f"Not yet — it isn't overdue under {law.get('state_name')} law. "
                                "I'll draft the letter the day it is."}
    path = letters.generate_demand_letter(case, law)
    db.update_case(case_id, {"demand_letter_path": str(path),
                             "demand_letter_at": deadlines.today().isoformat()}, owner)
    return {"case_id": case_id, "pdf_path": str(path),
            "note": "Template automation, not legal advice. User sends the letter themselves.",
            "user_message": "Your demand letter is ready — it cites your state statute and the "
                            "missed deadline. Send it certified mail or email, then tell me "
                            "when they pay."}


@server.tool()
def setup_billing(case_id: str) -> dict:
    """Create the Stripe customer and card SetupIntent for the 25% contingency
    fee (charged off-session only after the user confirms a recovery)."""
    owner = require_owner()
    case, err = _owned_case(case_id, owner)
    if err:
        return err
    setup_key = f"{billing.CONNECTOR}-setup-{owner}-{case_id}"
    customer_id = case.get("stripe_customer_id")
    if not customer_id:
        res = billing.setup_customer(case["employee_name"], case.get("employee_email", ""),
                                     idempotency_key=setup_key)
        if "error" in res:
            return {"error": f"stripe customer creation failed: {res['error']}",
                    "user_message": "The card setup hit a snag on my end — let's try again in a moment."}
        customer_id = res["customer_id"]
        db.update_case(case_id, {"stripe_customer_id": customer_id}, owner)
    setup = billing.create_card_setup(customer_id, idempotency_key=setup_key)
    if "error" in setup:
        return {"error": f"stripe setup intent failed: {setup['error']}",
                "user_message": "The card setup hit a snag on my end — let's try again in a moment."}
    db.update_case(case_id, {"stripe_setup_intent_id": setup["setup_intent_id"]}, owner)
    return {"customer_id": customer_id, "setup_intent_id": setup["setup_intent_id"],
            "client_secret": setup["client_secret"],
            "fee_rate": billing.FEE_RATE, "fee_disclosure": FEE_DISCLOSURE,
            "user_message": f"Almost done — save your card, and remember: {FEE_DISCLOSURE}"}


@server.tool()
def confirm_recovery(case_id: str, amount: float) -> dict:
    """User confirms wages were recovered; charges the 25% contingency fee
    off-session against the saved card."""
    owner = require_owner()
    case, err = _owned_case(case_id, owner)
    if err:
        return err
    if not case.get("stripe_customer_id"):
        return {"error": "billing not set up: run setup_billing first",
                "user_message": "No card on file yet — let's set up billing first, then confirm the recovery."}
    if case.get("fee_status") == "charged":
        return {"error": "contingency fee already charged for this case",
                "user_message": "The 25% fee for this case was already charged — nothing more to do."}
    owed = (case.get("wages_owed_cents") or 0) / 100
    if owed > 0 and amount > 2 * owed:
        return {"error": "asserted recovery exceeds 2x wages owed on the case",
                "user_message": f"That recovery amount ({money(int(round(amount * 100)))}) is more than "
                                f"twice the {money(case['wages_owed_cents'])} owed on this case — I won't charge "
                                "against that number. Double-check the amount you received and try again."}
    fee_cents = billing.contingency_cents(amount)
    res = billing.charge_fee(case["stripe_customer_id"], fee_cents,
                             f"final-paycheck contingency fee (25%) for case {case_id}",
                             idempotency_key=f"{billing.CONNECTOR}-fee-{owner}-{case_id}")
    if "error" in res:
        return {"error": f"stripe charge failed: {res['error']}",
                "user_message": "The charge didn't go through — your card may need attention. No fee was taken."}
    amount_cents = int(round(amount * 100))
    db.update_case(case_id, {
        "recovered_amount_cents": amount_cents, "fee_cents": fee_cents,
        "fee_payment_intent_id": res["payment_intent_id"], "fee_status": "charged",
    }, owner)
    return {"case_id": case_id, "recovered_amount": amount, "fee_rate": billing.FEE_RATE,
            "fee_charged": fee_cents / 100, "payment_intent_id": res["payment_intent_id"],
            "user_message": f"Done — {money(fee_cents)} fee charged on your {money(amount_cents)} "
                            f"recovery. You kept {money(amount_cents - fee_cents)}. Congratulations!"}


def main():
    import uvicorn
    port = int(os.environ.get("MCP_PORT", "8575"))
    uvicorn.run(create_mcp_app(), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
