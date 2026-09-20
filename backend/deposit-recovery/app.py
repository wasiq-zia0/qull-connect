"""Deposit Recovery connector API.

Agent-native: every response carries `user_message` — a warm, ready-to-speak
sentence the agent can say verbatim. The agent is the UI; there is no app
screen. Every flow is demoable inside a plain chat transcript.

Golden path: move detected -> case tracked -> deadline passes -> one tap ->
demand letter -> recovery confirmed -> 25% fee. Nothing in the golden path
needs more than a trigger plus one confirmation.
"""
import base64
import os
import uuid
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from typing import Literal

from src.laws import get_state, list_states, requires_forwarding_date
from src.engine import case_status
from src.letters import build_pdf
from src import db
from src.bus import emit_move
from src.billing import (
    setup_customer, create_card_setup, charge_fee,
    contingency_cents, FEE_RATE, CONNECTOR,
)
from src.identity import IdentityMiddleware, IdentityError, require_owner
from src.limits import RateLimitMiddleware, BodySizeLimitMiddleware

ROOT = Path(__file__).resolve().parent
LETTER_DIR = ROOT / "letters_out"
LETTER_DIR.mkdir(exist_ok=True)

FEE_DISCLOSURE = (
    "You will be charged 25% of the recovered deposit, only if you confirm "
    "the recovery. No charge otherwise. You can cancel any time before "
    "confirming; see TERMS.md."
)

FORWARDING_MSG = (
    "forwarding_date is required in {abbr}: the legal deadline runs from the "
    "date you provided your forwarding address."
)

db.init_db()

_PROD = os.environ.get("ENV") == "production"
_PUBLIC_DOCS = os.environ.get("PUBLIC_DOCS") == "1"

app = FastAPI(
    title="Deposit Recovery API",
    version="0.2.0",
    docs_url="/docs" if (_PUBLIC_DOCS or not _PROD) else None,
    redoc_url="/redoc" if (_PUBLIC_DOCS or not _PROD) else None,
    openapi_url="/openapi.json" if (_PUBLIC_DOCS or not _PROD) else None,
)

# Added last runs first: identity is checked before rate/body limits.
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(IdentityMiddleware)


# ---------------------------------------------------------------- errors ---
@app.exception_handler(IdentityError)
async def _identity_exc(request: Request, exc: IdentityError):
    return JSONResponse(
        status_code=401,
        content={"detail": str(exc), "user_message": str(exc)},
    )


@app.get("/health")
def health():
    """Public liveness probe (orchestrator)."""
    return {"status": "ok", "service": "deposit-recovery"}


# ---------------------------------------------------------------- errors ---
@app.exception_handler(HTTPException)
async def _http_exc(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "user_message": str(exc.detail)},
    )


@app.exception_handler(RequestValidationError)
async def _validation_exc(request: Request, exc: RequestValidationError):
    errs = jsonable_encoder(exc.errors())
    first = errs[0] if errs else {}
    loc = ".".join(str(x) for x in first.get("loc", []) if x != "body")
    msg = (f"There's a problem with '{loc}': {first.get('msg')}. Mind checking it?"
           if loc else "I couldn't make sense of that request — mind checking the fields?")
    return JSONResponse(status_code=422,
                        content={"detail": errs, "user_message": msg})


# ----------------------------------------------------------------- models ---
class CaseIn(BaseModel):
    tenant_name: str = Field(min_length=1, max_length=200)
    tenant_forwarding_address: str = Field(min_length=1, max_length=300)
    state: str = Field(min_length=2, max_length=2, description="US state abbreviation")
    move_out: date
    forwarding_date: date | None = None
    deposit: float = Field(gt=0, le=100_000_000)
    landlord_name: str = Field(min_length=1, max_length=200)
    landlord_address: str = Field(min_length=1, max_length=300)
    rental_address: str = Field(min_length=1, max_length=300)

    @field_validator("state")
    @classmethod
    def _norm_state(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("tenant_name", "tenant_forwarding_address",
                     "landlord_name", "landlord_address", "rental_address")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class RecoveryIn(BaseModel):
    amount_recovered: float = Field(gt=0, le=100_000_000,
                                    description="Deposit amount the tenant got back")


class LifeEventIn(BaseModel):
    event_type: str = Field(min_length=1, max_length=50)
    payload: dict = Field(default_factory=dict)


class LetterStatusIn(BaseModel):
    """The user reviews the prepared demand letter, then confirms it was sent."""
    status: Literal["approved", "sent-confirmed-by-user"]


class PromoteIn(BaseModel):
    """Fields the agent collected conversationally to complete a draft."""
    tenant_name: str | None = Field(default=None, max_length=200)
    tenant_forwarding_address: str | None = Field(default=None, max_length=300)
    state: str | None = Field(default=None, max_length=2)
    move_out: date | None = None
    forwarding_date: date | None = None
    deposit: float | None = Field(default=None, gt=0, le=100_000_000)
    landlord_name: str | None = Field(default=None, max_length=200)
    landlord_address: str | None = Field(default=None, max_length=300)
    rental_address: str | None = Field(default=None, max_length=300)


# ----------------------------------------------------------------- helpers --
def _money(n: float) -> str:
    return f"${n:,.2f}"


def _status_for(case: dict) -> dict:
    try:
        return case_status(
            case["state"], date.fromisoformat(case["move_out"]), case["deposit"],
            date.fromisoformat(case["forwarding_date"]) if case.get("forwarding_date") else None,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


def _case_nudge(status: dict) -> str:
    """The proactive sentence for a freshly computed case status."""
    state, dep = status["state"], _money(status["deposit"])
    days = status.get("deadline_days")
    if status["status"] == "overdue":
        overdue = -status["days_remaining"]
        return (f"Your {state} landlord had {days} days to return your {dep} deposit. "
                f"It's day {overdue + (days or 0)} — the deadline passed "
                f"{overdue} days ago. Want me to prepare the demand letter? "
                "You'll review and send it.")
    if status["status"] == "waiting":
        return (f"I'm tracking your {dep} deposit in {state}. Your landlord has until "
                f"{status['deadline']} ({status['days_remaining']} days) to return it. "
                "I'll draft the demand letter the moment that passes — nothing for you to do.")
    return (f"{state} doesn't set a fixed return deadline, so I've logged your case and "
            "we'll follow the notification process in the statute instead.")


def _check_forwarding(state_abbr: str, forwarding_date) -> None:
    if requires_forwarding_date(state_abbr) and not forwarding_date:
        raise HTTPException(400, FORWARDING_MSG.format(abbr=state_abbr))


def _row_to_case_fields(payload: CaseIn) -> dict:
    row = payload.model_dump()
    row["move_out"] = row["move_out"].isoformat()
    if row["forwarding_date"]:
        row["forwarding_date"] = row["forwarding_date"].isoformat()
    return row


def _create_case_row(fields: dict, emit: bool = False,
                     owner: str | None = None) -> tuple[str, dict]:
    cid = uuid.uuid4().hex[:12]
    db.insert_case(cid, fields, owner_id=owner)
    if emit:
        emit_move({"state": fields["state"], "move_out": fields["move_out"],
                   "deposit": fields["deposit"], "case_id": cid})
    return cid, _status_for(fields)


def _card_state(case: dict) -> str:
    """Pollable card-save state for GET .../billing/status."""
    if not case.get("stripe_customer_id"):
        return "none"
    return {"card_pending": "pending", "fee_charged": "ready",
            "fee_failed": "failed"}.get(case.get("billing_status") or "", "none")


def _draft_visible(draft: dict | None, owner: str) -> dict | None:
    """A draft is visible unless it is owned by someone else.

    Service-created drafts (owner_id NULL) stay visible until claimed.
    """
    if not draft:
        return None
    if draft.get("owner_id") and draft["owner_id"] != owner:
        return None
    return draft


# ------------------------------------------------------------- state laws ---
@app.get("/api/state-laws")
def api_list_states():
    owner = require_owner()
    states = [{"abbr": s["abbr"], "state": s["state"],
               "deadline_days": s["deadline_days"],
               "statute": s["statute"]} for s in list_states()]
    return {"states": states,
            "user_message": "I know the deposit-return deadline for all 50 states plus DC — tell me which state you moved out of."}


@app.get("/api/state-laws/{abbr}")
def api_get_state(abbr: str):
    owner = require_owner()
    try:
        law = get_state(abbr)
    except KeyError:
        raise HTTPException(404, "Unknown state")
    basis = ("from the day you gave your landlord a forwarding address"
             if requires_forwarding_date(law["abbr"]) else "from your move-out day")
    return {**law,
            "user_message": (f"In {law['state']}, landlords get {law['deadline_days']} days "
                             f"{basis} to return your deposit ({law['statute']}).")}


# ------------------------------------------------------------------- cases --
@app.post("/api/cases")
def api_create_case(payload: CaseIn):
    owner = require_owner()
    try:
        get_state(payload.state)
    except KeyError:
        raise HTTPException(400, "Unknown state abbreviation")
    _check_forwarding(payload.state, payload.forwarding_date)
    cid, status = _create_case_row(_row_to_case_fields(payload), emit=True, owner=owner)
    return {"case_id": cid, **status, "user_message": _case_nudge(status)}


@app.get("/api/cases/{cid}")
def api_get_case(cid: str):
    owner = require_owner()
    case = db.get_case(cid, owner_id=owner)
    if not case:
        raise HTTPException(404, "I don't have a case with that ID — want me to open one?")
    status = _status_for(case)
    return {"case_id": cid, **case, **status, "user_message": _case_nudge(status)}


@app.post("/api/cases/{cid}/demand-letter")
def api_demand_letter(cid: str):
    owner = require_owner()
    case = db.get_case(cid, owner_id=owner)
    if not case:
        raise HTTPException(404, "I don't have a case with that ID — want me to open one?")
    _check_forwarding(case["state"], case.get("forwarding_date"))
    status = _status_for(case)
    if status["status"] != "overdue":
        raise HTTPException(
            400,
            f"Not yet — your {status['state']} deadline is {status.get('deadline') or 'not fixed'}, "
            f"and the demand letter goes out the day after it passes (status: {status['status']}). "
            "I'm watching the clock; I'll nudge you when it's time.")
    path = LETTER_DIR / f"demand-letter-{cid}.pdf"
    try:
        build_pdf(case, str(path))
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.update_case(cid, {"letter_status": "draft"}, owner_id=owner)
    law = get_state(case["state"])
    pdf_b64 = base64.b64encode(path.read_bytes()).decode()
    return {
        "case_id": cid,
        "filename": f"demand-letter-{cid}.pdf",
        "pdf_base64": pdf_b64,
        "letter_status": "draft",
        "user_message": (
            f"Your demand letter is ready — it cites {law['statute']} and gives your landlord "
            f"10 days to return your {_money(case['deposit'])}. Review it and send it yourself, "
            "then tell me the moment your deposit lands so I can close your case. "
            "This is template automation, not legal advice."),
    }


@app.post("/api/cases/{cid}/letter-status")
def api_letter_status(cid: str, payload: LetterStatusIn):
    """Record the user's review/send decision on the prepared demand letter.

    The connector only ever *prepares* the letter — the user approves and
    sends it. Statuses: `approved` (reviewed, ready to send),
    `sent-confirmed-by-user` (the user confirms they sent it).
    """
    owner = require_owner()
    case = db.get_case(cid, owner_id=owner)
    if not case:
        raise HTTPException(404, "I don't have a case with that ID — want me to open one?")
    db.update_case(cid, {"letter_status": payload.status}, owner_id=owner)
    msg = ("Got it — the letter is approved and ready for you to send."
           if payload.status == "approved"
           else "Confirmed — the letter is sent. I'll keep watching for your deposit.")
    return {"case_id": cid, "letter_status": payload.status, "user_message": msg}


# ----------------------------------------------------------------- billing --
@app.post("/api/cases/{cid}/billing/setup")
def api_billing_setup(cid: str):
    """Create the Stripe customer and a card-setup intent for the contingency fee."""
    owner = require_owner()
    case = db.get_case(cid, owner_id=owner)
    if not case:
        raise HTTPException(404, "I don't have a case with that ID — want me to open one?")
    if case.get("stripe_customer_id"):
        raise HTTPException(400, "Billing is already set up for this case — no need to do it twice.")
    cust = setup_customer(case["tenant_name"])
    if "error" in cust:
        raise HTTPException(502, f"Stripe customer creation failed: {cust['error']}")
    setup = create_card_setup(
        cust["customer_id"],
        idempotency_key=f"{CONNECTOR}-setup-{owner}-{cid}")
    if "error" in setup:
        raise HTTPException(502, f"Stripe card setup failed: {setup['error']}")
    db.update_case(cid, {"stripe_customer_id": cust["customer_id"],
                         "billing_status": "card_pending"}, owner_id=owner)
    return {"case_id": cid, "stripe_customer_id": cust["customer_id"],
            "client_secret": setup["client_secret"],
            "fee_disclosure": FEE_DISCLOSURE,
            "user_message": ("Here's the deal, in plain terms: " + FEE_DISCLOSURE +
                             " Save your card below and you're done — nothing happens until you tell me the deposit came back.")}


@app.get("/api/cases/{cid}/billing/status")
def api_billing_status(cid: str):
    """Pollable card-save + billing state for a case."""
    owner = require_owner()
    case = db.get_case(cid, owner_id=owner)
    if not case:
        raise HTTPException(404, "I don't have a case with that ID — want me to open one?")
    billing_status = case.get("billing_status") or "none"
    card_state = _card_state(case)
    state_msg = {
        "none": "No card on file yet — set up billing when you're ready.",
        "pending": "Your card is saved and ready. Nothing is charged until you confirm the recovery.",
        "ready": "Your card is saved and the fee for this case is settled.",
        "failed": "The last fee charge failed — your card was not billed. We can retry once you've confirmed.",
    }.get(card_state, "Billing state unknown — ask me to check.")
    return {"case_id": cid, "billing_status": billing_status,
            "card_state": card_state, "fee_disclosure": FEE_DISCLOSURE,
            "user_message": state_msg}


@app.post("/api/cases/{cid}/recovery-confirmed")
def api_recovery_confirmed(cid: str, payload: RecoveryIn):
    """Tenant confirms the deposit came back: charge the 25% contingency fee off-session."""
    owner = require_owner()
    case = db.get_case(cid, owner_id=owner)
    if not case:
        raise HTTPException(404, "I don't have a case with that ID — want me to open one?")
    if case.get("billing_status") == "fee_charged":
        raise HTTPException(400, "The fee for this case was already charged — you're all settled.")
    customer_id = case.get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(400, "No card on file yet — set up billing first, then confirm the recovery.")
    # Tampering/implausibility guard: the fee is 25% of the asserted recovery,
    # but a recovery larger than the deposit on file can't be right.
    if payload.amount_recovered > float(case["deposit"]):
        raise HTTPException(
            400, f"That doesn't add up — you confirmed {_money(payload.amount_recovered)} recovered, "
                 f"but the deposit on this case is {_money(case['deposit'])}. "
                 "Mind double-checking the amount?")
    fee_cents = contingency_cents(payload.amount_recovered)
    result = charge_fee(customer_id, fee_cents,
                        f"Deposit recovery fee ({int(FEE_RATE*100)}% of {_money(payload.amount_recovered)}) - case {cid}",
                        idempotency_key=f"{CONNECTOR}-fee-{owner}-{cid}")
    if "error" in result:
        db.update_case(cid, {"billing_status": "fee_failed"}, owner_id=owner)
        raise HTTPException(502, f"The fee charge didn't go through: {result['error']}")
    db.update_case(cid, {"billing_status": "fee_charged",
                         "amount_recovered": payload.amount_recovered,
                         "fee_charged_cents": fee_cents}, owner_id=owner)
    return {"case_id": cid, "amount_recovered": payload.amount_recovered,
            "fee_rate": FEE_RATE, "fee_charged_cents": fee_cents,
            "payment_intent_id": result["payment_intent_id"],
            "user_message": (f"Your {_money(payload.amount_recovered)} is back — congratulations. "
                             f"The 25% fee is {_money(fee_cents / 100)}, charged now that you've "
                             "confirmed the recovery. Case closed.")}


# ------------------------------------------------------------- life events --
DRAFT_FIELDS = ("tenant_name", "tenant_forwarding_address", "state", "move_out",
                "forwarding_date", "deposit", "landlord_name",
                "landlord_address", "rental_address")

_DRAFT_LABELS = {
    "tenant_name": "your name",
    "tenant_forwarding_address": "your forwarding address",
    "state": "the state you moved out of",
    "move_out": "your move-out date",
    "forwarding_date": "the date you gave your landlord your forwarding address",
    "deposit": "your deposit amount",
    "landlord_name": "your landlord's name",
    "landlord_address": "your landlord's address",
    "rental_address": "the rental address",
}


def _draft_from_payload(event_type: str, payload: dict) -> dict:
    fields = {"event_type": event_type, "payload": payload}
    for f in DRAFT_FIELDS:
        v = payload.get(f)
        if f == "state" and isinstance(v, str):
            v = v.strip().upper()
        fields[f] = v
    return fields


def _missing_fields(fields: dict) -> list[str]:
    # forwarding_date is only required where the legal clock runs from it
    # (TX, CT, MN, WY); everywhere else the clock runs from move-out.
    state = fields.get("state")
    required = [f for f in DRAFT_FIELDS
                if f != "forwarding_date"
                or (state and requires_forwarding_date(state))]
    return [f for f in required if not fields.get(f)]


def _draft_nudge(draft_id: str, fields: dict, missing: list[str]) -> str:
    state = fields.get("state")
    state_name = state
    try:
        state_name = get_state(state)["state"] if state else None
    except KeyError:
        pass
    if missing == ["forwarding_date"] and state:
        days = ""
        try:
            days = f" {(date.today() - date.fromisoformat(fields['move_out'])).days} days ago"
        except Exception:
            pass
        dep = f" with your {_money(fields['deposit'])} deposit on the line" if fields.get("deposit") else ""
        return (f"Quick one — I see you moved out of {state_name or state}{days}{dep}. "
                f"In {state_name or state}, the landlord's {get_state(state)['deadline_days']}-day clock "
                "starts the day you gave them your forwarding address. When did you send it?")
    labels = [_DRAFT_LABELS[m] for m in missing]
    where = f" from {state_name}" if state_name else ""
    return (f"I caught your move{where} — let me get your deposit back on the radar. "
            f"I still need {', '.join(labels[:-1]) + ' and ' + labels[-1] if len(labels) > 1 else labels[0]}. "
            f"What's {_DRAFT_LABELS[missing[0]]}?")


@app.post("/api/life-events")
def api_life_events(event: LifeEventIn):
    """Receive a fan-out life event from the shared bus.

    Service-key authenticated (see IdentityMiddleware) — there is no user
    owner here, so drafts/cases created from the bus store owner_id=NULL.
    The promote endpoint stamps the caller's owner when the draft is claimed.
    Creates a real case when the payload is complete, otherwise a draft case
    pre-filled with everything the payload provides. Always returns the
    proactive nudge in `user_message`.
    """
    if event.event_type != "move":
        raise HTTPException(
            400, f"I don't act on '{event.event_type}' events yet — I watch for moves.")
    fields = _draft_from_payload("move", event.payload or {})
    if fields.get("state"):
        try:
            get_state(fields["state"])
        except KeyError:
            raise HTTPException(400, f"Unknown state abbreviation: {fields['state']}")
    missing = _missing_fields(fields)
    if not missing:
        cid, status = _create_case_row(fields, emit=False, owner=None)  # came from the bus; don't re-emit
        return {"case_id": cid, "draft": False, **status,
                "user_message": _case_nudge(status)}
    draft_id = uuid.uuid4().hex[:12]
    db.insert_draft(draft_id, fields, owner_id=None)  # bus-created: unclaimed until promote
    return {"draft_id": draft_id, "draft": True,
            "missing": missing,
            "prefilled": {k: v for k, v in fields.items() if k in DRAFT_FIELDS and v},
            "user_message": _draft_nudge(draft_id, fields, missing)}


@app.get("/api/drafts/{draft_id}")
def api_get_draft(draft_id: str):
    owner = require_owner()
    draft = _draft_visible(db.get_draft(draft_id), owner)
    if not draft:
        raise HTTPException(404, "I don't have a draft with that ID.")
    missing = _missing_fields(draft)
    return {"draft_id": draft_id, **draft, "missing": missing,
            "user_message": _draft_nudge(draft_id, draft, missing) if missing
            else "That draft is complete — promote it and I'll open your case."}


@app.post("/api/drafts/{draft_id}/promote")
def api_promote_draft(draft_id: str, extra: PromoteIn):
    """Merge conversationally collected fields into a draft and open the case.

    Claiming: the new case is stamped with the caller's owner_id.
    """
    owner = require_owner()
    draft = _draft_visible(db.get_draft(draft_id), owner)
    if not draft:
        raise HTTPException(404, "I don't have a draft with that ID.")
    fields = dict(draft)
    for k, v in extra.model_dump(exclude_none=True).items():
        if k == "state" and isinstance(v, str):
            v = v.strip().upper()
        fields[k] = v.isoformat() if isinstance(v, date) else (v.strip() if isinstance(v, str) else v)
    if fields.get("state"):
        try:
            get_state(fields["state"])
        except KeyError:
            raise HTTPException(400, f"Unknown state abbreviation: {fields['state']}")
    missing = _missing_fields(fields)
    if missing:
        labels = [_DRAFT_LABELS[m] for m in missing]
        raise HTTPException(400, f"Still need {', '.join(labels)} before I can open your case.")
    cid, status = _create_case_row(fields, emit=False, owner=owner)
    db.delete_draft(draft_id)
    return {"case_id": cid, "draft": False, **status,
            "user_message": _case_nudge(status)}


# Public marketing site is only servable in dev; in production this
# process serves the authenticated API only.
if not _PROD:
    app.mount("/", StaticFiles(directory=str(ROOT / "static"), html=True), name="static")
