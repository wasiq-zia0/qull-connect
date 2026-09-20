"""Final-paycheck connector: recover unpaid final wages via state-deadline demand letters.

Agent-native design: the agent is the UI. EVERY response carries a
`user_message` field — a warm, ready-to-speak sentence the agent can say
verbatim. Every flow is demoable inside a plain chat transcript.

Golden path: life-event trigger -> one tap -> done.
Draft cases are created from job_change events; one POST to /confirm
activates them.

Fee: 25% of confirmed recovered wages, charged off-session via Stripe
after the user confirms a recovery. Nothing is ever sent externally by
this service; demand letters are generated PDFs the user sends themselves.
"""
import os
from typing import Literal
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ConfigDict, ValidationError, BaseModel, Field, field_validator

import billing
import db
import deadlines
import letters
from identity import IdentityMiddleware, IdentityError, require_owner
from limits import RateLimitMiddleware, BodySizeLimitMiddleware
from sanitize import money

_PROD = os.environ.get("ENV") == "production"
_PUBLIC_DOCS = os.environ.get("PUBLIC_DOCS") == "1"
app = FastAPI(title="final-paycheck", version="0.2.0",
              docs_url="/docs" if (_PUBLIC_DOCS or not _PROD) else None,
              redoc_url="/redoc" if (_PUBLIC_DOCS or not _PROD) else None,
              openapi_url="/openapi.json" if (_PUBLIC_DOCS or not _PROD) else None)

# Middleware: added last runs first, so IdentityMiddleware enforces identity
# before rate limiting or body-size checks see the request.
app.add_middleware(IdentityMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(BodySizeLimitMiddleware)

FEE_DISCLOSURE = (
    "You will be charged 25% of the recovered wages, only if you confirm "
    "the recovery. No charge otherwise. You can cancel at any time before a "
    "charge — simply do not confirm a recovery, or ask support to close your case."
)


@app.on_event("startup")
def _startup():
    db.init_db()


@app.exception_handler(IdentityError)
async def _identity_error(request: Request, exc: IdentityError):  # noqa: ARG001
    return JSONResponse(status_code=401, content={
        "ok": False, "error": "unauthorized", "user_message": str(exc)})


# ---------------------------------------------------------------- models

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class BillingConsentIn(StrictModel):
    accept_fee_terms: Literal[True] = Field(description="The user accepted the disclosed fee terms before opening Stripe checkout.")


class FeeConsentIn(StrictModel):
    confirm_fee: Literal[True] = Field(description="The user explicitly approved this exact fee after reviewing it.")
    fee_amount_cents: int = Field(ge=1, le=100_000_000, description="The exact disclosed fee approved by the user, in cents.")


def _validate_input(model, data):
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(422, "Invalid or incomplete fields: " + ", ".join(".".join(map(str, e["loc"])) for e in exc.errors()))


def _billing_failure(result):
    # Only authenticated owners receive any payment-action token.
    safe = {k: result[k] for k in ("error", "code", "status", "payment_intent_id", "authorization_url", "setup_url", "currency", "amount_cents") if k in result}
    code = result.get("code")
    status = (202 if code == "payment_processing" else
              402 if code in {"payment_action_required", "payment_declined", "setup_required", "setup_incomplete", "payment_not_completed"} else
              409 if code in {"billing_conflict", "payment_not_captured", "payment_pending"} else
              422 if code in {"invalid_fee", "consent_required"} else 503)
    return JSONResponse(status_code=status,
                        content={**safe, "user_message": result.get("error", "The payment has not been confirmed. Check billing status before retrying.")})


class Intake(StrictModel):
    employee_name: str = Field(min_length=1, max_length=120)
    employee_email: str = Field(default="", max_length=120)
    employer_name: str = Field(min_length=1, max_length=120)
    employer_address: str = Field(default="", max_length=500)
    state: str = Field(min_length=2, max_length=2)
    last_day_worked: date
    termination_type: str  # fired | laid_off | quit
    wages_owed: float = Field(gt=0, lt=10_000_000)
    pay_period: str = Field(default="", max_length=80)
    next_payday: date | None = None
    forwarding_address: str = Field(default="", max_length=500)

    @field_validator("termination_type")
    @classmethod
    def _tt(cls, v: str) -> str:
        v = v.lower()
        if v not in ("fired", "laid_off", "quit"):
            raise ValueError("termination_type must be fired, laid_off, or quit")
        return v

    @field_validator("state")
    @classmethod
    def _st(cls, v: str) -> str:
        v = v.upper()
        if deadlines.get_state(v) is None:
            raise ValueError(f"unknown state abbreviation: {v}")
        return v


class ConfirmDraft(StrictModel):
    """One-tap activation of a draft case; fill any fields still missing."""
    employee_name: str | None = Field(default=None, max_length=120)
    employer_name: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, min_length=2, max_length=2)
    last_day_worked: date | None = None
    termination_type: str | None = None
    wages_owed: float | None = Field(default=None, gt=0, lt=10_000_000)
    next_payday: date | None = None
    forwarding_address: str | None = Field(default=None, max_length=500)


class RecoveryConfirmed(FeeConsentIn):
    amount: float = Field(gt=0, lt=10_000_000)


class LifeEvent(StrictModel):
    event_type: str
    payload: dict = Field(default_factory=dict)


# ---------------------------------------------------------------- helpers

def msg(payload: dict, user_message: str) -> dict:
    """Every response carries a speakable user_message for the agent."""
    return {**payload, "user_message": user_message}


def _case_or_404(case_id: str, owner: str) -> dict:
    """Fetch a case belonging to owner. A life-event draft with no owner yet
    is claimed by the first owner who presents its unguessable ID; anyone
    else's case is indistinguishable from a missing one (404)."""
    case = db.get_case(case_id, owner)
    if case is not None:
        return case
    claimed = db.claim_case(case_id, owner)
    if claimed is not None:
        return claimed
    raise HTTPException(404, f"case not found: {case_id}")


def _card_state(case: dict) -> str:
    """Pollable card state for the billing flow."""
    if not case.get("stripe_customer_id"):
        return "none"
    status = case.get("fee_status") or ""
    if status == "charged":
        return "ready"
    if status == "failed":
        return "failed"
    return "pending"


def _law_for(case: dict) -> dict:
    if case.get("is_draft"):
        return {"status": "draft", "state_name": (deadlines.get_state(case["state"]) or {}).get("name"), "missing": [k for k in ("employee_name", "employer_name", "last_day_worked", "wages_owed_cents") if not case.get(k)]}
    law = deadlines.compute_deadline(
        case["state"],
        case["termination_type"] or "fired",
        date.fromisoformat(case["last_day_worked"]) if case.get("last_day_worked") else deadlines.today(),
        date.fromisoformat(case["next_payday"]) if case.get("next_payday") else None,
    )
    if "error" in law:
        raise HTTPException(400, law["error"])
    return law


def _describe_rule(state: dict, termination_type: str) -> str:
    """Plain-language deadline phrase, e.g. 'due on your last day'."""
    kind = "fired" if termination_type in ("fired", "laid_off") else "quit"
    rule = state[kind]

    def phrase(r: dict) -> str:
        k = r.get("kind")
        if k == "immediate":
            return "due on your last day"
        if k == "calendar_days":
            n = r["days"]
            return f"due within {n} day{'s' if n != 1 else ''} of your last day"
        if k == "working_days":
            n = r["days"]
            return f"due within {n} working day{'s' if n != 1 else ''} of your last day"
        if k == "next_payday":
            return "due on the next regular payday"
        if k == "none":
            return "has no specific state deadline"
        if k in ("earlier_of", "later_of"):
            parts = [phrase(o) for o in r["options"]]
            joiner = " or ".join(parts)
            return f"due {joiner}, whichever is {'earlier' if k == 'earlier_of' else 'later'}"
        return "due under state law"

    return phrase(rule)


def _public_case(case: dict, law: dict) -> dict:
    return {
        "id": case["id"],
        "created_at": case["created_at"],
        "draft": bool(case.get("is_draft")),
        "employee_name": case["employee_name"],
        "employer_name": case["employer_name"],
        "state": case["state"],
        "state_name": law.get("state_name"),
        "last_day_worked": case["last_day_worked"],
        "termination_type": case["termination_type"],
        "wages_owed": case["wages_owed_cents"] / 100,
        "pay_period": case["pay_period"],
        "next_payday": case.get("next_payday"),
        "deadline": law.get("deadline"),
        "status": law.get("status"),
        "days_overdue": law.get("days_overdue"),
        "days_remaining": law.get("days_remaining"),
        "statute": law.get("statute"),
        "penalty_note": law.get("penalty_note"),
        "note": law.get("note"),
        "flags": law.get("flags", []),
        "missing": law.get("missing"),
        "stripe_customer_id": case.get("stripe_customer_id"),
        "fee_status": case.get("fee_status"),
        "recovered_amount": (case["recovered_amount_cents"] / 100
                             if case.get("recovered_amount_cents") is not None else None),
        "demand_letter_generated": case.get("demand_letter_path") is not None,
    }


def _status_message(case: dict, law: dict) -> str:
    wages = money(case["wages_owed_cents"])
    employer = case["employer_name"] or "your former employer"
    state_name = law.get("state_name") or case["state"]
    status = law.get("status")
    if status == "draft":
        return "This draft is incomplete. Supply the missing intake fields before preparing a letter or setting up billing."
    if status == "needs_review":
        return "Your case is saved. The state deadline needs review; I can prepare a factual unpaid-wages request for you to send."
    if status == "overdue":
        d = law["days_overdue"]
        return (f"Your {wages} final paycheck from {employer} is {d} day{'s' if d != 1 else ''} "
                f"past due under {state_name} law. I can draft your demand letter now — one tap.")
    if status == "waiting":
        d = law["days_remaining"]
        return (f"Watching your {wages} from {employer}: {state_name} law says it's due "
                f"in {d} day{'s' if d != 1 else ''}. Check the case again after that date.")
    if status == "needs_info":
        return (f"To pin down your {state_name} deadline I need your next regular payday date — "
                f"what was it?")
    if status == "no_state_deadline":
        return (f"{state_name} doesn't set a specific final-paycheck deadline, so I can't "
                f"auto-escalate this one — but an employment lawyer still might help. Want "
                f"guidance on filing a wage claim?")
    return f"Your case for {wages} from {employer} is being tracked."


# ---------------------------------------------------------------- routes

@app.get("/health")
def health():
    return msg({"ok": True, "service": "final-paycheck", "version": "0.2.0"},
               "Final Paycheck Recovery is up and running.")


@app.get("/api/state-laws")
def state_laws():
    require_owner()
    laws = deadlines.load_laws()
    return msg({
        "version": laws["version"],
        "last_verified": laws["last_verified"],
        "disclaimer": laws["disclaimer"],
        "states": laws["states"],
    }, "Factual wage-request letters are available nationwide; source-reviewed deadline support is currently limited to Nevada discharge cases.")


@app.get("/api/state-laws/{abbr}")
def state_law(abbr: str):
    require_owner()
    state = deadlines.get_state(abbr)
    if state is None:
        raise HTTPException(404, f"unknown state abbreviation: {abbr.upper()}")
    fired = _describe_rule(state, "fired")
    quit_ = _describe_rule(state, "quit")
    return msg(state, "This reference requires review before relying on a deadline. Review status: " + state["review_status"])


@app.post("/api/cases", status_code=201)
def create_case(intake: Intake):
    owner = require_owner()
    last_day = intake.last_day_worked
    law = deadlines.compute_deadline(intake.state, intake.termination_type, last_day, intake.next_payday)
    if "error" in law:
        raise HTTPException(400, law["error"])
    case = db.create_case({
        "employee_name": intake.employee_name.strip(),
        "employee_email": intake.employee_email.strip(),
        "employer_name": intake.employer_name.strip(),
        "employer_address": intake.employer_address.strip(),
        "state": intake.state,
        "last_day_worked": last_day.isoformat(),
        "termination_type": intake.termination_type,
        "wages_owed_cents": int(round(intake.wages_owed * 100)),
        "pay_period": intake.pay_period.strip(),
        "next_payday": intake.next_payday.isoformat() if intake.next_payday else None,
        "forwarding_address": intake.forwarding_address.strip(),
        "deadline": law.get("deadline"),
        "status": law.get("status"),
        "raw_intake": intake.model_dump(mode="json"),
    }, owner_id=owner)
    wages = money(case["wages_owed_cents"])
    dl = law.get("deadline") or "no fixed state deadline"
    return msg(_public_case(case, law),
               f"Got it — I'm watching your {wages} final paycheck from {case['employer_name']}. "
               f"{law.get('state_name')} law says it was due {dl}. I'll draft the demand letter "
               f"after you request it and the verified deadline has passed.")


@app.get("/api/cases/{case_id}")
def get_case(case_id: str):
    owner = require_owner()
    case = _case_or_404(case_id, owner)
    law = _law_for(case)
    return msg(_public_case(case, law), _status_message(case, law))


@app.post("/api/cases/{case_id}/confirm")
def confirm_case(case_id: str, body: ConfirmDraft):
    """One-tap activation of a draft case (created from a job_change life event)."""
    owner = require_owner()
    case = _case_or_404(case_id, owner)
    if not case.get("is_draft"):
        raise HTTPException(409, "Only an incomplete draft can be confirmed. Open a new case to correct completed intake.")
    patch = {}
    if body.employee_name: patch["employee_name"] = body.employee_name.strip()
    if body.employer_name: patch["employer_name"] = body.employer_name.strip()
    if body.state:
        st = body.state.upper()
        if deadlines.get_state(st) is None:
            raise HTTPException(400, f"unknown state abbreviation: {st}")
        patch["state"] = st
    if body.last_day_worked: patch["last_day_worked"] = body.last_day_worked.isoformat()
    if body.termination_type:
        tt = body.termination_type.lower()
        if tt not in ("fired", "laid_off", "quit"):
            raise HTTPException(400, "termination_type must be fired, laid_off, or quit")
        patch["termination_type"] = tt
    if body.wages_owed is not None: patch["wages_owed_cents"] = int(round(body.wages_owed * 100))
    if body.next_payday: patch["next_payday"] = body.next_payday.isoformat()
    if body.forwarding_address: patch["forwarding_address"] = body.forwarding_address.strip()
    merged = {**case, **patch}
    full = {k: merged.get(k) for k in Intake.model_fields if k in merged}
    full["wages_owed"] = (merged.get("wages_owed_cents") or 0) / 100
    _validate_input(Intake, full)
    patch["is_draft"] = 0
    if not db.activate_draft(case_id, owner, patch):
        raise HTTPException(409, "The draft was already activated or changed.")
    case = db.get_case(case_id, owner)
    law = _law_for(case)
    db.update_case(case_id, {"deadline": law.get("deadline"), "status": law.get("status")}, owner)
    case = db.get_case(case_id, owner)
    wages = money(case["wages_owed_cents"]) if case["wages_owed_cents"] else "your final paycheck"
    employer = case["employer_name"] or "your former employer"
    return msg(_public_case(case, law),
               f"You're in — I'm now tracking {wages} from {employer}. {_status_message(case, law)}")


@app.post("/api/cases/{case_id}/demand-letter")
def demand_letter(case_id: str):
    owner = require_owner()
    case = _case_or_404(case_id, owner)
    law = _law_for(case)
    if law.get("status") not in ("overdue", "needs_review"):
        return JSONResponse(status_code=400, content=msg(
            {"ok": False, "status": law.get("status")},
            f"Not yet — your paycheck isn't overdue under {law.get('state_name')} law "
            f"(due {law.get('deadline') or 'once I know your next payday'}). "
            f"Request a draft once the verified deadline has passed."))
    path = letters.generate_demand_letter(case, law)
    db.update_case(case_id, {"demand_letter_path": str(path),
                             "demand_letter_at": deadlines.today().isoformat()}, owner)
    return FileResponse(str(path), media_type="application/pdf",
                        filename=f"demand_letter_{case_id}.pdf")


@app.post("/api/cases/{case_id}/billing/setup")
def billing_setup(case_id: str, consent: BillingConsentIn):
    """Open Stripe-hosted card setup after the user accepts the disclosed fee. No charge."""
    owner = require_owner()
    case = _case_or_404(case_id, owner)
    if not case:
        raise HTTPException(404, "Unknown case")
    if case.get("fee_status") == "charged":
        raise HTTPException(409, "The fee is already settled.")
    if case.get("is_draft"):
        raise HTTPException(409, "Complete the intake before setting up billing.")
    setup_key = f"{billing.CONNECTOR}-setup-{owner}-{case_id}"
    customer_id = case.get("stripe_customer_id")
    if not customer_id:
        customer = billing.setup_customer(case["employee_name"], case.get("employee_email", ""), idempotency_key=setup_key)
        if "error" in customer:
            return _billing_failure(customer)
        customer_id = customer["customer_id"]
        db.update_case(case_id, {"stripe_customer_id": customer_id}, owner)
    setup = billing.create_card_setup(customer_id, idempotency_key=setup_key)
    if "error" in setup:
        return _billing_failure(setup)
    db.update_case(case_id, {"stripe_setup_intent_id": setup.get("setup_intent_id"), "checkout_session_id": setup.get("checkout_session_id"), "fee_status": "card_pending", "fee_terms_accepted_at": datetime.now(timezone.utc).isoformat()}, owner)
    return {"case_id": case_id, "stripe_customer_id": customer_id,
            "setup_url": setup.get("setup_url"), "checkout_session_id": setup.get("checkout_session_id"),
            "setup_intent_id": setup.get("setup_intent_id"), "fee_disclosure": FEE_DISCLOSURE,
            "user_message": FEE_DISCLOSURE + " Open setup_url to save a payment method securely with Stripe. Saving it does not charge a fee."}


@app.get("/api/cases/{case_id}/billing/status")
def billing_status(case_id: str):
    """Verify saved-card setup with Stripe; a pending checkout is never a saved card."""
    owner = require_owner()
    case = _case_or_404(case_id, owner)
    if not case:
        raise HTTPException(404, "Unknown case")
    verified = {"status": "none"}
    if case.get("stripe_customer_id"):
        verified = billing.retrieve_card_setup(case["stripe_customer_id"],
            setup_intent_id=case.get("stripe_setup_intent_id"),
            checkout_session_id=case.get("checkout_session_id"))
    state = "ready" if verified.get("status") == "succeeded" and "error" not in verified else "none" if not case.get("stripe_customer_id") else "pending"
    return {"case_id": case_id, "billing_status": case.get("fee_status") or "none",
            "card_state": state, "setup_status": verified.get("status"),
            "fee_disclosure": FEE_DISCLOSURE,
            "user_message": "Your payment method is saved. Review and explicitly approve the fee before payment." if state == "ready" else "Card setup has not been confirmed. Open the Stripe setup link and complete it."}


@app.post("/api/cases/{case_id}/recovery-confirmed")
def recovery_confirmed(case_id: str, body: RecoveryConfirmed):
    """User confirms wages were recovered; charge the 25% contingency fee
    off-session against the saved card.

    The charge always goes to the case's STORED stripe_customer_id — the
    request carries only the recovered amount, never card details. The fee is
    25% of the actual received wages, bounded at the wages owed stored
    on the case: a larger assertion is rejected (400) rather than charged."""
    owner = require_owner()
    case = _case_or_404(case_id, owner)
    customer_id = case.get("stripe_customer_id")
    if not customer_id:
        return JSONResponse(status_code=400, content=msg(
            {"ok": False},
            "I can't charge the fee yet — no card on file. Run billing setup first, "
            "then confirm the recovery."))
    if case.get("fee_status") == "charged":
        return JSONResponse(status_code=400, content=msg(
            {"ok": False},
            "The 25% fee for this case was already charged — nothing more to do."))
    owed = (case.get("wages_owed_cents") or 0) / 100
    if owed <= 0 or body.amount > owed:
        return JSONResponse(status_code=400, content=msg(
            {"ok": False, "asserted_amount": body.amount, "wages_owed": owed},
            f"That recovery amount ({money(int(round(body.amount * 100)))}) is more than "
            f"the {money(case['wages_owed_cents'])} owed on this case — I won't charge "
            "against that number. Double-check the amount you received and try again."))
    amount_cents = int(round(body.amount * 100))
    fee_cents = billing.contingency_cents(body.amount)
    if body.fee_amount_cents != fee_cents:
        raise HTTPException(409, {"error": "fee_changed", "expected_fee_amount_cents": fee_cents, "user_message": "Review the current fee and explicitly approve that amount."})
    if not case.get("fee_terms_accepted_at"):
        raise HTTPException(409, "Accept the fee terms through billing setup first.")
    db.update_case(case_id, {"fee_confirmed_at": datetime.now(timezone.utc).isoformat()}, owner)
    res = billing.charge_fee(
        customer_id, fee_cents,
        f"final-paycheck contingency fee (25%) for case {case_id}",
        idempotency_key=f"{billing.CONNECTOR}-fee-{owner}-{case_id}",
        setup_intent_id=case.get("stripe_setup_intent_id"),
        checkout_session_id=case.get("checkout_session_id"), consent=body.confirm_fee,
    )
    if res.get("status") != "succeeded" and "error" not in res:
        res = {**res, "error": "Payment has not succeeded.", "code": "payment_pending"}
    if "error" in res:
        return _billing_failure(res)
    db.update_case(case_id, {
        "recovered_amount_cents": amount_cents,
        "fee_cents": fee_cents,
        "fee_payment_intent_id": res["payment_intent_id"],
        "fee_status": "charged",
    }, owner)
    kept = money(amount_cents - fee_cents)
    return msg({
        "case_id": case_id,
        "recovered_amount": body.amount,
        "fee_rate": billing.FEE_RATE,
        "fee_charged": fee_cents / 100,
        "payment_intent_id": res["payment_intent_id"],
        "status": res["status"],
    }, f"Done — {money(fee_cents)} fee charged on your {money(amount_cents)} recovery. "
       f"You kept {kept}. Congratulations!")


@app.post("/api/life-events")
def life_events(body: LifeEvent):
    """Receive fan-out from the shared life-events bus. Creates a draft case
    from a job_change payload and returns the proactive nudge as user_message."""
    if body.event_type != "job_change":
        raise HTTPException(400, f"unsupported event_type for this connector: {body.event_type}")
    owner = require_owner()
    p = _validate_input(ConfirmDraft, body.payload or {}).model_dump(mode="json", exclude_none=True)
    state = (p.get("state") or "").upper()
    if not state or deadlines.get_state(state) is None:
        return msg({"ok": False, "draft": False},
                   "Heads up — it looks like you changed jobs. Which state were you "
                   "employed in? I'll check whether your final paycheck is overdue.")
    law_state = deadlines.get_state(state)
    employer = (p.get("employer_name") or "").strip() or "your former employer"
    tt = (p.get("termination_type") or "fired").lower()
    if tt not in ("fired", "laid_off", "quit"):
        tt = "fired"
    wages = p.get("wages_owed")
    last_day = p.get("last_day_worked")

    case = db.create_case({
        "employee_name": (p.get("employee_name") or "").strip(),
        "employee_email": (p.get("employee_email") or "").strip(),
        "employer_name": employer if employer != "your former employer" else "",
        "employer_address": "",
        "state": state,
        "last_day_worked": last_day or "",
        "termination_type": tt,
        "wages_owed_cents": int(round(float(wages) * 100)) if wages else 0,
        "pay_period": "",
        "next_payday": p.get("next_payday"),
        "forwarding_address": "",
        "deadline": None,
        "status": "draft",
        "is_draft": 1,
    }, owner_id=owner)

    rule_phrase = _describe_rule(law_state, tt)
    missing = [f for f, v in {
        "employer_name": not p.get("employer_name"),
        "last_day_worked": not last_day,
        "wages_owed": not wages,
        "termination_type": not p.get("termination_type"),
    }.items() if v]

    days_ago = ""
    if last_day:
        try:
            delta = (deadlines.today() - date.fromisoformat(last_day)).days
            if delta >= 0:
                days_ago = f" {delta} day{'s' if delta != 1 else ''} ago"
        except ValueError:
            pass
    wages_bit = f"final {money(int(round(float(wages) * 100)))} paycheck" if wages else "final paycheck"
    nudge = (f"You left {employer}{days_ago} and your {wages_bit} hasn't arrived. "
             f"{law_state['name']} law says it was {rule_phrase}. "
             f"Want me to draft the demand letter? One tap.")
    return msg({
        "ok": True, "draft": True, "case_id": case["id"],
        "missing_fields": missing,
        "rule": rule_phrase,
    }, nudge)


@app.exception_handler(Exception)
async def _unhandled(request, exc):  # noqa: ARG001
    if isinstance(exc, HTTPException):
        return JSONResponse({"error": exc.detail, "user_message":
                             "Something didn't look right with that request — mind trying again?"},
                            status_code=exc.status_code)
    return JSONResponse({"error": "internal server error", "user_message":
                         "Hmm, something went wrong on my end. Give me a moment and try again."},
                        status_code=500)


class DeleteDataIn(StrictModel):
    confirm_delete: Literal[True] = Field(description="The user explicitly confirms deleting their local records and generated documents.")


@app.get("/api/me/data")
def export_my_data():
    """Export only the authenticated caller's local records."""
    return {"records": db.export_owner(require_owner()), "user_message": "This export contains your local service records."}


@app.delete("/api/me/data")
def delete_my_data(consent: DeleteDataIn):
    """Delete local records and documents. Stripe/payment audit records remain separately retained."""
    deleted = db.delete_owner(require_owner())
    for row in deleted.get("cases", []):
        (letters.LETTERS_DIR / f"demand_letter_{row['id']}.pdf").unlink(missing_ok=True)
    return {"deleted": {table: len(rows) for table, rows in deleted.items()},
            "user_message": "Your local records and generated documents have been deleted. Stripe transaction records and the payment audit ledger are retained separately; contact support for related requests."}


class FeeQuoteIn(StrictModel):
    amount: float = Field(gt=0, le=1_000_000, description="The actual recovered amount to quote; this operation does not charge.")


@app.post("/api/cases/{case_id}/billing/quote")
def quote_fee(case_id: str, payload: FeeQuoteIn):
    """Show the exact rounded fee for review; no card setup, confirmation or payment occurs."""
    owner = require_owner()
    case = _case_or_404(case_id, owner)
    if not case:
        raise HTTPException(404, "Unknown case")
    if payload.amount > case["wages_owed_cents"] / 100:
        raise HTTPException(400, "The recovery amount exceeds the recorded case amount.")
    fee = billing.contingency_cents(payload.amount)
    return {"case_id": case_id, "currency": "USD", "recovery_amount": payload.amount,
            "fee_rate": 0.25, "fee_amount_cents": fee, "charged": False,
            "user_message": f"The fee would be {fee / 100:.2f} USD. Review it, then explicitly approve this exact amount if you want to pay."}


# Shared authenticated API schema and payment return page.
try:
    from api_support import install_api_contract
except ImportError:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from api_support import install_api_contract
install_api_contract(app, "final-paycheck", app.title)
