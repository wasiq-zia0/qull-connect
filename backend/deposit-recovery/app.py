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
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ConfigDict, ValidationError, BaseModel, Field, field_validator
from typing import Literal

from src.laws import get_state, list_states, requires_forwarding_date
from src.engine import case_status
from src.letters import build_pdf
from src import db
from src.bus import emit_move
from src.billing import (
    setup_customer, create_card_setup, charge_fee, retrieve_card_setup,
    contingency_cents, FEE_RATE, CONNECTOR,
)
from src.identity import IdentityMiddleware, IdentityError, require_owner
from src.limits import RateLimitMiddleware, BodySizeLimitMiddleware

ROOT = Path(__file__).resolve().parent
LETTER_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data")) / "letters"
LETTER_DIR.mkdir(parents=True, exist_ok=True)

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
app.add_middleware(IdentityMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(BodySizeLimitMiddleware)


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


class CaseIn(StrictModel):
    tenant_name: str = Field(min_length=1, max_length=200)
    tenant_forwarding_address: str = Field(min_length=1, max_length=300)
    state: str = Field(min_length=2, max_length=2, description="US state abbreviation")
    move_out: date = Field(description="Date you vacated and surrendered possession of the rental property.")
    tenancy_end: date | None = Field(default=None, description="Date the tenancy legally ended; required for a Connecticut deadline estimate.")
    forwarding_date: date | None = Field(default=None, description="Date the landlord received your written forwarding address, not merely the date you sent it.")
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




class RecoveryIn(FeeConsentIn):
    amount_recovered: float = Field(gt=0, le=100_000_000,
                                    description="Deposit amount the tenant got back")


class LifeEventIn(StrictModel):
    event_type: str = Field(min_length=1, max_length=50)
    payload: dict = Field(default_factory=dict)


class LetterStatusIn(StrictModel):
    """The user reviews the prepared demand letter, then confirms it was sent."""
    status: Literal["approved", "sent-confirmed-by-user"]


class PromoteIn(StrictModel):
    """Fields the agent collected conversationally to complete a draft."""
    tenant_name: str | None = Field(default=None, max_length=200)
    tenant_forwarding_address: str | None = Field(default=None, max_length=300)
    state: str | None = Field(default=None, max_length=2)
    move_out: date | None = None
    tenancy_end: date | None = None
    forwarding_date: date | None = Field(default=None, description="Date the landlord received your written forwarding address, not merely the date you sent it.")
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
            tenancy_end=date.fromisoformat(case["tenancy_end"]) if case.get("tenancy_end") else None,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


def _case_nudge(status: dict) -> str:
    if status["status"] == "needs_review":
        return f"Your {status['state']} case is saved. The legal deadline needs review; I can prepare a factual request for return and accounting for you to send."
    if status["status"] == "waiting":
        return f"The preliminary return/accounting deadline is {status['deadline']}. Check the case after that date; no automatic monitoring or sending is scheduled."
    return f"The preliminary deadline {status['deadline']} has passed. Review any deductions or notices received, then request a draft letter to send yourself."


def _check_forwarding(state_abbr: str, forwarding_date) -> None:
    # Missing timing is surfaced as needs_review, not invented.
    get_state(state_abbr)


def _row_to_case_fields(payload: CaseIn) -> dict:
    row = payload.model_dump()
    row["move_out"] = row["move_out"].isoformat()
    if row.get("tenancy_end"):
        row["tenancy_end"] = row["tenancy_end"].isoformat()
    if row["forwarding_date"]:
        row["forwarding_date"] = row["forwarding_date"].isoformat()
    return row


def _create_case_row(fields: dict, emit: bool = False,
                     owner: str | None = None) -> tuple[str, dict]:
    fields = _row_to_case_fields(_validate_input(CaseIn, {k: fields[k] for k in CaseIn.model_fields if fields.get(k) is not None}))
    status = _status_for(fields)
    cid = uuid.uuid4().hex
    db.insert_case(cid, fields, owner_id=owner)
    return cid, status


def _card_state(case: dict) -> str:
    """Pollable card-save state for GET .../billing/status."""
    if not case.get("stripe_customer_id"):
        return "none"
    return {"card_pending": "pending", "fee_charged": "ready",
            "fee_failed": "failed"}.get(case.get("billing_status") or "", "none")


def _draft_visible(draft: dict | None, owner: str) -> dict | None:
    return draft if draft and draft.get("owner_id") == owner else None


# ------------------------------------------------------------- state laws ---
@app.get("/api/state-laws")
def api_list_states():
    owner = require_owner()
    states = [{"abbr": s["abbr"], "state": s["state"],
               "deadline_days": s["deadline_days"] if s.get("deadline_verified") else None,
               "deadline_verified": s.get("deadline_verified", False), "review_status": s["review_status"],
               "official_source_url": s["official_source_url"], "statute": s["statute"]} for s in list_states()]
    return {"states": states,
            "user_message": "I provide case intake and factual request letters nationwide. Verified deadline calculations currently cover limited CA, CT and TX rules; other jurisdictions need review."}


@app.get("/api/state-laws/{abbr}")
def api_get_state(abbr: str):
    owner = require_owner()
    try:
        law = get_state(abbr)
    except KeyError:
        raise HTTPException(404, "Unknown state")
    return {**law, "user_message": "Review the official source and rule scope before relying on this reference. Deadline verification status: " + law["review_status"]}



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
    if status["status"] == "waiting":
        raise HTTPException(
            400,
            f"Not yet — your {status['state']} deadline is {status.get('deadline') or 'not fixed'}, "
            f"and the demand letter goes out the day after it passes (status: {status['status']}). "
            "Check the case again after the deadline.")
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
        "letter_type": "factual_return_request",
        "user_message": "Your return-and-accounting request is ready. Review the facts, then send it yourself. No liability or penalty entitlement is asserted.",
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
    if not case.get("letter_status"):
        raise HTTPException(409, "Generate and review the letter before updating its status.")
    db.update_case(cid, {"letter_status": payload.status}, owner_id=owner)
    msg = ("Got it — the letter is approved and ready for you to send."
           if payload.status == "approved"
           else "Recorded that you sent the letter. Return here to report any recovery.")
    return {"case_id": cid, "letter_status": payload.status, "user_message": msg}


# ----------------------------------------------------------------- billing --
@app.post("/api/cases/{cid}/billing/setup")
def api_billing_setup(cid: str, consent: BillingConsentIn):
    """Open Stripe-hosted card setup after the user accepts the disclosed fee. No charge."""
    owner = require_owner()
    case = db.get_case(cid, owner_id=owner)
    if not case:
        raise HTTPException(404, "Unknown case")
    if case.get("billing_status") == "fee_charged":
        raise HTTPException(409, "The fee is already settled.")
    setup_key = f"{CONNECTOR}-setup-{owner}-{cid}"
    customer_id = case.get("stripe_customer_id")
    if not customer_id:
        customer = setup_customer(case["tenant_name"], "", idempotency_key=setup_key)
        if "error" in customer:
            return _billing_failure(customer)
        customer_id = customer["customer_id"]
        db.update_case(cid, {"stripe_customer_id": customer_id}, owner_id=owner)
    setup = create_card_setup(customer_id, idempotency_key=setup_key)
    if "error" in setup:
        return _billing_failure(setup)
    db.update_case(cid, {"setup_intent_id": setup.get("setup_intent_id"), "checkout_session_id": setup.get("checkout_session_id"), "billing_status": "card_pending", "fee_terms_accepted_at": datetime.now(timezone.utc).isoformat()}, owner_id=owner)
    return {"case_id": cid, "stripe_customer_id": customer_id,
            "setup_url": setup.get("setup_url"), "checkout_session_id": setup.get("checkout_session_id"),
            "setup_intent_id": setup.get("setup_intent_id"), "fee_disclosure": FEE_DISCLOSURE,
            "user_message": FEE_DISCLOSURE + " Open setup_url to save a payment method securely with Stripe. Saving it does not charge a fee."}


@app.get("/api/cases/{cid}/billing/status")
def api_billing_status(cid: str):
    """Verify saved-card setup with Stripe; a pending checkout is never a saved card."""
    owner = require_owner()
    case = db.get_case(cid, owner_id=owner)
    if not case:
        raise HTTPException(404, "Unknown case")
    verified = {"status": "none"}
    if case.get("stripe_customer_id"):
        verified = retrieve_card_setup(case["stripe_customer_id"],
            setup_intent_id=case.get("setup_intent_id"),
            checkout_session_id=case.get("checkout_session_id"))
    state = "ready" if verified.get("status") == "succeeded" and "error" not in verified else "none" if not case.get("stripe_customer_id") else "pending"
    return {"case_id": cid, "billing_status": case.get("billing_status") or "none",
            "card_state": state, "setup_status": verified.get("status"),
            "fee_disclosure": FEE_DISCLOSURE,
            "user_message": "Your payment method is saved. Review and explicitly approve the fee before payment." if state == "ready" else "Card setup has not been confirmed. Open the Stripe setup link and complete it."}


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
    if payload.fee_amount_cents != fee_cents:
        raise HTTPException(409, {"error": "fee_changed", "expected_fee_amount_cents": fee_cents, "user_message": "Review the current fee and explicitly approve that amount."})
    if not case.get("fee_terms_accepted_at"):
        raise HTTPException(409, "Accept the fee terms through billing setup first.")
    db.update_case(cid, {"fee_confirmed_at": datetime.now(timezone.utc).isoformat()}, owner_id=owner)
    result = charge_fee(customer_id, fee_cents,
                        f"Deposit recovery fee ({int(FEE_RATE*100)}% of {_money(payload.amount_recovered)}) - case {cid}",
                        idempotency_key=f"{CONNECTOR}-fee-{owner}-{cid}",
        setup_intent_id=case.get("setup_intent_id"),
        checkout_session_id=case.get("checkout_session_id"), consent=payload.confirm_fee)
    if result.get("status") != "succeeded" and "error" not in result:
        result = {**result, "error": "Payment has not succeeded.", "code": "payment_pending"}
    if "error" in result:
        db.update_case(cid, {"billing_status": "fee_failed"}, owner_id=owner)
        return _billing_failure(result)
    db.update_case(cid, {"billing_status": "fee_charged",
                         "amount_recovered": payload.amount_recovered,
                         "fee_charged_cents": fee_cents, "payment_intent_id": result["payment_intent_id"]}, owner_id=owner)
    return {"case_id": cid, "amount_recovered": payload.amount_recovered,
            "fee_rate": FEE_RATE, "fee_charged_cents": fee_cents,
            "payment_intent_id": result["payment_intent_id"],
            "user_message": (f"Your {_money(payload.amount_recovered)} is back — congratulations. "
                             f"The 25% fee is {_money(fee_cents / 100)}, charged now that you've "
                             "confirmed the recovery. Case closed.")}


# ------------------------------------------------------------- life events --
DRAFT_FIELDS = ("tenant_name", "tenant_forwarding_address", "state", "move_out",
                "forwarding_date", "tenancy_end", "deposit", "landlord_name",
                "landlord_address", "rental_address")

_DRAFT_LABELS = {
    "tenant_name": "your name",
    "tenant_forwarding_address": "your forwarding address",
    "state": "the state you moved out of",
    "move_out": "your move-out date",
    "tenancy_end": "the date your tenancy legally ended",
    "forwarding_date": "the date the landlord received your written forwarding address, if known",
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
    required = [f for f in DRAFT_FIELDS if f not in ("forwarding_date", "tenancy_end")]
    return [f for f in required if not fields.get(f)]


def _draft_nudge(draft_id: str, fields: dict, missing: list[str]) -> str:
    state = fields.get("state")
    state_name = state
    try:
        state_name = get_state(state)["state"] if state else None
    except KeyError:
        pass
    labels = [_DRAFT_LABELS[m] for m in missing]
    where = f" from {state_name}" if state_name else ""
    return (f"I caught your move{where} — let me get your deposit back on the radar. "
            f"I still need {', '.join(labels[:-1]) + ' and ' + labels[-1] if len(labels) > 1 else labels[0]}. "
            f"What's {_DRAFT_LABELS[missing[0]]}?")


@app.post("/api/life-events")
def api_life_events(event: LifeEventIn):
    """Receive a fan-out life event from the shared bus.

    The caller's verified API key determines ownership. Client-supplied
    owner IDs and legacy records without an owner are never claimable.
    Creates a real case when the payload is complete, otherwise a draft case
    pre-filled with everything the payload provides. Always returns the
    proactive nudge in `user_message`.
    """
    if event.event_type != "move":
        raise HTTPException(
            400, f"I don't act on '{event.event_type}' events yet — I watch for moves.")
    owner = require_owner()
    validated = _validate_input(PromoteIn, event.payload or {})
    fields = _draft_from_payload("move", validated.model_dump(mode="json", exclude_none=True))
    if fields.get("state"):
        try:
            get_state(fields["state"])
        except KeyError:
            raise HTTPException(400, f"Unknown state abbreviation: {fields['state']}")
    missing = _missing_fields(fields)
    if not missing:
        cid, status = _create_case_row(fields, emit=False, owner=owner)
        return {"case_id": cid, "draft": False, **status,
                "user_message": _case_nudge(status)}
    draft_id = uuid.uuid4().hex
    db.insert_draft(draft_id, fields, owner_id=owner)
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
    fields = _row_to_case_fields(_validate_input(CaseIn, {k: fields[k] for k in CaseIn.model_fields if fields.get(k) is not None}))
    status = _status_for(fields)
    cid = uuid.uuid4().hex
    if not db.promote_draft(draft_id, owner, cid, fields):
        raise HTTPException(409, "The draft has already been promoted or deleted.")
    return {"case_id": cid, "draft": False, **status,
            "user_message": _case_nudge(status)}


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
        (LETTER_DIR / f"demand-letter-{row['id']}.pdf").unlink(missing_ok=True)
    return {"deleted": {table: len(rows) for table, rows in deleted.items()},
            "user_message": "Your local records and generated documents have been deleted. Stripe transaction records and the payment audit ledger are retained separately; contact support for related requests."}


class FeeQuoteIn(StrictModel):
    amount_recovered: float = Field(gt=0, le=1_000_000, description="The actual recovered amount to quote; this operation does not charge.")


@app.post("/api/cases/{cid}/billing/quote")
def quote_fee(cid: str, payload: FeeQuoteIn):
    """Show the exact rounded fee for review; no card setup, confirmation or payment occurs."""
    owner = require_owner()
    case = db.get_case(cid, owner_id=owner)
    if not case:
        raise HTTPException(404, "Unknown case")
    if payload.amount_recovered > case["deposit"]:
        raise HTTPException(400, "The recovery amount exceeds the recorded case amount.")
    fee = contingency_cents(payload.amount_recovered)
    return {"case_id": cid, "currency": "USD", "recovery_amount": payload.amount_recovered,
            "fee_rate": 0.25, "fee_amount_cents": fee, "charged": False,
            "user_message": f"The fee would be {fee / 100:.2f} USD. Review it, then explicitly approve this exact amount if you want to pay."}


# Public marketing site is only servable in dev; in production this
# process serves the authenticated API only.
if not _PROD:
    app.mount("/", StaticFiles(directory=str(ROOT / "static"), html=True), name="static")


# Shared authenticated API schema and payment return page.
try:
    from api_support import install_api_contract
except ImportError:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from api_support import install_api_contract
install_api_contract(app, "deposit-recovery", app.title)
