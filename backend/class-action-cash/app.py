"""Class Action Cash — FastAPI REST API.

The agent is the UI: every response carries a `user_message` field — a warm,
ready-to-speak sentence the agent can say verbatim.
"""
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ConfigDict, ValidationError, BaseModel, Field
from typing import Optional
import os
from datetime import datetime, timezone
from typing import Literal

import core
import db
from identity import IdentityMiddleware, IdentityError, require_owner
from limits import RateLimitMiddleware, BodySizeLimitMiddleware

_PROD = os.environ.get("ENV") == "production"
_PUBLIC_DOCS = os.environ.get("PUBLIC_DOCS") == "1"
app = FastAPI(title="Class Action Cash", version="0.1.0",
              description="Matches user-supplied receipt excerpts against verified open settlements, "
                          "prepares claim packs. 20% fee on confirmed payout only.",
              docs_url="/docs" if (_PUBLIC_DOCS or not _PROD) else None,
              redoc_url="/redoc" if (_PUBLIC_DOCS or not _PROD) else None,
              openapi_url="/openapi.json" if (_PUBLIC_DOCS or not _PROD) else None)

# Middleware: added last runs first, so IdentityMiddleware enforces identity
# before rate limiting or body-size checks see the request.
app.add_middleware(IdentityMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(BodySizeLimitMiddleware)


# ---------- error handling: always speakable ----------

@app.exception_handler(IdentityError)
async def _identity_error(request: Request, exc: IdentityError):
    return JSONResponse(status_code=401, content={
        "ok": False, "error": "unauthorized",
        "user_message": str(exc)})


def _card_state(bill: dict | None) -> str:
    """Pollable card state for the billing flow."""
    if not bill or not bill.get("stripe_customer_id"):
        return "none"
    status = bill.get("status") or ""
    if status == "card_pending":
        return "pending"
    if status in ("billed", "fee_charged"):
        return "ready"
    if status in ("failed", "fee_failed"):
        return "failed"
    return "pending"

@app.exception_handler(HTTPException)
async def _http_error(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"ok": False, "error": exc.detail,
                 "user_message": f"Something went wrong: {exc.detail}"})


@app.exception_handler(KeyError)
async def _missing_record(request: Request, exc: KeyError):
    return JSONResponse(status_code=404, content={"ok": False, "error": "Unknown match", "user_message": "No match found for your account."})


@app.exception_handler(ValueError)
async def _value_error(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"ok": False, "error": str(exc), "user_message": str(exc)})


@app.exception_handler(Exception)
async def _unexpected_error(request: Request, exc: Exception):
    if isinstance(exc, (KeyError, ValueError)):
        return JSONResponse(status_code=400, content={
            "ok": False, "error": str(exc),
            "user_message": "That didn't look right: " + str(exc)})
    return JSONResponse(status_code=500, content={
        "ok": False, "error": "internal error",
        "user_message": "The request could not be completed. Check billing status before retrying a payment."})


# ---------- models ----------

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


class ReceiptIn(StrictModel):
    id: str = Field(default="", max_length=120)
    sender: str = Field(default="", max_length=300)
    subject: str = Field(default="", max_length=500)
    date: str = Field(default="", max_length=40)
    snippet: str = Field(min_length=1, max_length=4000)


class ScanRequest(StrictModel):
    source: Literal["receipts", "fixtures"] = "receipts"
    receipts: list[ReceiptIn] = Field(default_factory=list, max_length=100)


class ClaimPackRequest(StrictModel):
    eligibility_confirmed: Literal[True]
    full_name: Optional[str] = Field(default="", max_length=200)
    email: Optional[str] = Field(default="", max_length=200)
    address: Optional[str] = Field(default="", max_length=300)


class BillingSetupRequest(BillingConsentIn):
    name: str = Field(min_length=1, max_length=200)
    email: Optional[str] = Field(default="", max_length=200)


class PayoutConfirmRequest(FeeConsentIn):
    amount: float = Field(gt=0, le=1_000_000, description="Confirmed payout received, in dollars")
    currency: Literal["USD"] = "USD"


class LifeEventRequest(StrictModel):
    event_type: str = Field(max_length=100)
    payload: dict = Field(default_factory=dict)


# ---------- routes ----------

@app.get("/health")
def health():
    return {"ok": True, "status": "ok", "service": "class-action-cash",
            "user_message": "Class Action Cash is up — I'll scan your receipts against open class-action settlements."}


@app.get("/api/settlements")
def settlements():
    require_owner()
    return core.get_settlements()


@app.post("/api/scan")
def scan(req: ScanRequest):
    owner = require_owner()
    try:
        return core.scan(req.source, owner=owner, receipts=[r.model_dump() for r in req.receipts])
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/matches")
def matches():
    owner = require_owner()
    return core.list_matches(owner=owner)


@app.post("/api/matches/{match_id}/claim-pack")
def claim_pack(match_id: str, req: ClaimPackRequest):
    owner = require_owner()
    try:
        return core.generate_claim_pack(match_id, req.full_name, req.email, req.address,
                                        owner=owner, eligibility_confirmed=req.eligibility_confirmed)
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.post("/api/matches/{match_id}/billing/setup")
def billing_setup(match_id: str, req: BillingSetupRequest):
    """Create a Stripe customer + SetupIntent so the user can save a card.

    The response carries an explicit fee disclosure BEFORE any card is saved:
    20% of the confirmed payout, charged only if the user confirms the payout.
    """
    owner = require_owner()
    try:
        result = core.setup_billing(match_id, req.name, req.email, owner=owner, accept_fee_terms=req.accept_fee_terms)
        return _billing_failure(result) if result.get("error") else result
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.get("/api/matches/{match_id}/billing/status")
def billing_status(match_id: str):
    """Pollable card-save status: billing status, derived card_state, and the
    fee disclosure — the agent's signal for whether a charge can be attempted."""
    owner = require_owner()
    try:
        match = core._owned_match(match_id, owner)
    except KeyError as e:
        raise HTTPException(404, str(e))
    bill = core.db.get_billing(match_id, owner)
    verified = core.billing.retrieve_card_setup(bill["stripe_customer_id"], setup_intent_id=bill.get("setup_intent_id"), checkout_session_id=bill.get("checkout_session_id")) if bill and bill.get("stripe_customer_id") else {"status": "none"}
    card_state = "ready" if verified.get("status") == "succeeded" and "error" not in verified else "none" if not bill else "pending"
    return {
        "ok": True, "match_id": match_id,
        "billing_status": (bill or {}).get("status"),
        "card_state": card_state,
        "stripe_customer_id": (bill or {}).get("stripe_customer_id"),
        "fee_rate": core.FEE_RATE,
        "fee_cents": (bill or {}).get("fee_cents"),
        "fee_disclosure": core.FEE_DISCLOSURE,
        "user_message": (
            "Your card is saved and ready — I'll only charge the 20% fee after you "
            "confirm the payout." if card_state == "ready"
            else "No card on file yet — set up billing and save a card first."
            if card_state == "none"
            else f"Billing status: {(bill or {}).get('status')}. {core.FEE_DISCLOSURE}"),
    }


@app.post("/api/matches/{match_id}/payout-confirmed")
def payout_confirmed(match_id: str, req: PayoutConfirmRequest):
    """User confirms the settlement paid out -> charge the 20% fee off-session."""
    owner = require_owner()
    try:
        result = core.confirm_payout(match_id, req.amount, req.currency, owner=owner, confirm_fee=req.confirm_fee, fee_amount_cents=req.fee_amount_cents)
        return _billing_failure(result) if not result.get("ok") else result
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/life-events")
def life_events(req: LifeEventRequest):
    """Shared-bus receiver. Logs the match, returns the proactive nudge."""
    return core.receive_life_event(req.event_type, req.payload, owner=require_owner())


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
    return {"deleted": {table: len(rows) for table, rows in deleted.items()},
            "user_message": "Your local records and generated documents have been deleted. Stripe transaction records and the payment audit ledger are retained separately; contact support for related requests."}


class FeeQuoteIn(StrictModel):
    amount: float = Field(gt=0, le=1_000_000, description="The actual recovered amount to quote; this operation does not charge.")


@app.post("/api/matches/{match_id}/billing/quote")
def quote_fee(match_id: str, payload: FeeQuoteIn):
    """Show the exact rounded fee for review; no card setup, confirmation or payment occurs."""
    owner = require_owner()
    match = core._owned_match(match_id, owner)
    if not match:
        raise HTTPException(404, "Unknown match")
    if False:
        raise HTTPException(400, "The recovery amount exceeds the recorded case amount.")
    fee = int((__import__("decimal").Decimal(str(payload.amount)) * 20).quantize(__import__("decimal").Decimal("1"), rounding=__import__("decimal").ROUND_HALF_UP))
    return {"match_id": match_id, "currency": "USD", "recovery_amount": payload.amount,
            "fee_rate": 0.20, "fee_amount_cents": fee, "charged": False,
            "user_message": f"The fee would be {fee / 100:.2f} USD. Review it, then explicitly approve this exact amount if you want to pay."}


# Shared authenticated API schema and payment return page.
try:
    from api_support import install_api_contract
except ImportError:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from api_support import install_api_contract
install_api_contract(app, "class-action-cash", app.title)
