"""Class Action Cash — FastAPI REST API.

The agent is the UI: every response carries a `user_message` field — a warm,
ready-to-speak sentence the agent can say verbatim.
"""
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional
import os

import core
from identity import IdentityMiddleware, IdentityError, require_owner
from limits import RateLimitMiddleware, BodySizeLimitMiddleware

_PROD = os.environ.get("ENV") == "production"
_PUBLIC_DOCS = os.environ.get("PUBLIC_DOCS") == "1"
app = FastAPI(title="Class Action Cash", version="0.1.0",
              description="Matches Gmail receipts against open class-action settlements, "
                          "prepares claim packs. 20% fee on confirmed payout only.",
              docs_url="/docs" if (_PUBLIC_DOCS or not _PROD) else None,
              redoc_url="/redoc" if (_PUBLIC_DOCS or not _PROD) else None,
              openapi_url="/openapi.json" if (_PUBLIC_DOCS or not _PROD) else None)

# Middleware: added last runs first, so IdentityMiddleware enforces identity
# before rate limiting or body-size checks see the request.
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(IdentityMiddleware)


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


@app.exception_handler(Exception)
async def _unexpected_error(request: Request, exc: Exception):
    if isinstance(exc, (KeyError, ValueError)):
        return JSONResponse(status_code=400, content={
            "ok": False, "error": str(exc),
            "user_message": "That didn't look right: " + str(exc)})
    return JSONResponse(status_code=500, content={
        "ok": False, "error": "internal error",
        "user_message": "Something went wrong on my end — nothing was charged. Try again in a moment."})


# ---------- models ----------

class ScanRequest(BaseModel):
    source: str = Field(default="gmail", pattern="^(gmail|fixtures)$",
                        description="'gmail' scans the connected mailbox (read-only); "
                                    "'fixtures' uses demo receipts")


class ClaimPackRequest(BaseModel):
    full_name: Optional[str] = Field(default="", max_length=200)
    email: Optional[str] = Field(default="", max_length=200)
    address: Optional[str] = Field(default="", max_length=300)


class BillingSetupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: Optional[str] = Field(default="", max_length=200)


class PayoutConfirmRequest(BaseModel):
    amount: float = Field(gt=0, description="Confirmed payout received, in dollars")
    currency: str = Field(default="USD", max_length=3)


class LifeEventRequest(BaseModel):
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
        return core.scan(req.source, owner=owner)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/matches")
def matches():
    owner = require_owner()
    return core.list_matches(owner=owner)


@app.post("/api/matches/{match_id}/claim-pack")
def claim_pack(match_id: str, req: ClaimPackRequest = ClaimPackRequest()):
    owner = require_owner()
    try:
        return core.generate_claim_pack(match_id, req.full_name, req.email, req.address,
                                        owner=owner)
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
        return core.setup_billing(match_id, req.name, req.email, owner=owner)
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
    card_state = _card_state(bill)
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
        return core.confirm_payout(match_id, req.amount, req.currency, owner=owner)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/life-events")
def life_events(req: LifeEventRequest):
    """Shared-bus receiver. Logs the match, returns the proactive nudge."""
    return core.receive_life_event(req.event_type, req.payload)
