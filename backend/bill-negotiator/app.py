"""Bill-negotiator connector API.

Agent-native design: the agent is the UI — there is no app screen. EVERY
endpoint response carries a `user_message` field: a warm, ready-to-speak
sentence the agent can say verbatim. Every flow is demoable in a plain chat
transcript.

Flow:
  1. Intake (or a bill_spike life event) -> draft case
  2. Negotiation script pack (call + chat scripts) for the USER to use with
     their provider's retention team. The connector never contacts providers.
  3. User reports outcome -> documented savings computed
  4. Stripe customer + SetupIntent (card save; fee disclosed in plain language first)
  5. User confirms documented savings -> 35% fee charged off-session

Scripts are negotiation guidance only, not legal or financial advice.
"""
import os
from typing import Literal
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ConfigDict, ValidationError, BaseModel, Field, field_validator

from src import db
from src.db import init_db, create_case, get_case, update_case, claim_case, serialize
from src.scripts import script_pack, list_providers, clean_text
from src.billing import (setup_customer, create_card_setup, charge_fee, retrieve_card_setup, fee_cents,
                         FEE_RATE, CONNECTOR)
from src.identity import IdentityMiddleware, IdentityError, require_owner
from src.limits import RateLimitMiddleware, BodySizeLimitMiddleware

_PROD = os.environ.get("ENV") == "production"
_PUBLIC_DOCS = os.environ.get("PUBLIC_DOCS") == "1"
app = FastAPI(title="Bill Negotiator API", version="0.2.0",
              docs_url="/docs" if (_PUBLIC_DOCS or not _PROD) else None,
              redoc_url="/redoc" if (_PUBLIC_DOCS or not _PROD) else None,
              openapi_url="/openapi.json" if (_PUBLIC_DOCS or not _PROD) else None)

# Middleware: added last runs first, so IdentityMiddleware enforces identity
# before rate limiting or body-size checks see the request.
app.add_middleware(IdentityMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(BodySizeLimitMiddleware)

init_db()


@app.exception_handler(IdentityError)
async def _identity_error(request: Request, exc: IdentityError):
    return JSONResponse(status_code=401, content={
        "ok": False, "error": "unauthorized",
        "user_message": str(exc)})

FEE_DISCLOSURE = ("You will be charged 35% of your documented bill savings, only if you confirm "
                  "the new lower bill. No charge otherwise.")


# ---------- schemas ----------

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
    provider: str = Field(..., min_length=2, max_length=120, description="e.g. Comcast, Spectrum, AT&T, Verizon, Cox")
    service_type: Literal["internet", "cable", "phone", "bundle"] = Field(..., description="internet, cable, phone, or bundle")
    current_monthly_bill: float = Field(..., gt=0, le=100_000, description="Current bill in USD/month")
    prior_monthly_bill: float | None = Field(None, gt=0, le=100_000, description="What you used to pay (optional; enables spike detection)")
    promo_end_date: date | None = Field(None, description="Contract/promotion end date (YYYY-MM-DD)")
    account_tenure_months: int | None = Field(None, ge=0, description="How long you've been a customer")
    user_name: str | None = Field(None, min_length=1, max_length=120)


class OutcomeIn(StrictModel):
    success: bool = Field(True, description="False if negotiation produced no savings")
    new_monthly_bill: float | None = Field(None, ge=0, le=100_000, description="Negotiated monthly bill in USD")
    months_locked: int | None = Field(None, ge=1, le=36, description="Months the new rate is locked")


class LifeEventIn(StrictModel):
    event_type: str = Field(..., min_length=1)
    payload: dict = Field(default_factory=dict)


# ---------- helpers ----------

def _owned_case(cid: str, owner: str) -> dict:
    """Fetch a case belonging to owner. A life-event draft with no owner yet
    is claimed by the first owner who presents its unguessable ID; anyone
    else's case is indistinguishable from a missing one (404)."""
    case = get_case(cid, owner)
    if case:
        return case
    claimed = claim_case(cid, owner)
    if claimed:
        return claimed
    raise HTTPException(404, "Unknown case")


def _card_state(case: dict) -> str:
    """Pollable card state for the billing flow."""
    if not case.get("stripe_customer_id"):
        return "none"
    status = case.get("billing_status") or ""
    if status == "card_pending":
        return "pending"
    if status in ("fee_charged", "billed"):
        return "ready"
    if status in ("fee_failed", "failed"):
        return "failed"
    return "pending"


def _money(x: float) -> str:
    return f"${x:,.2f}" if x != int(x) else f"${int(x)}"


def _maybe_emit_spike(case: dict, prior: float | None) -> str | None:
    """Cross-service delivery is disabled until an owner-scoped transport exists."""
    return None


def bill_spike_nudge(provider: str, service_type: str,
                     prior: float | None, current: float) -> str:
    if prior and current and current > prior:
        return (f"Your {service_type} bill jumped from {_money(prior)} to {_money(current)} — "
                "Review what changed with your provider before asking for a discount. "
                "Want the script? One tap.")
    return (f"Your {provider} {service_type} bill changed — I've drafted your case and "
            "pulled the negotiation script. Want it? One tap.")


# ---------- endpoints ----------

@app.get("/health")
def health():
    return {"ok": True, "service": "bill-negotiator", "version": "0.2.0",
            "user_message": "Bill Negotiator is up and ready to save you money."}


@app.get("/api/providers")
def api_providers():
    owner = require_owner()  # noqa: F841 - identity enforced for all tenant endpoints
    providers = list_providers()
    return {"providers": providers,
            "user_message": f"I've got negotiation scripts ready for {len(providers)} providers — "
                            "just tell me yours and your current bill."}


@app.post("/api/cases", status_code=201)
def api_create_case(payload: CaseIn):
    owner = require_owner()
    case = create_case(
        provider=clean_text(payload.provider),
        service_type=clean_text(payload.service_type, max_len=60),
        current_monthly_bill=payload.current_monthly_bill,
        promo_end_date=payload.promo_end_date.isoformat() if payload.promo_end_date else None,
        account_tenure_months=payload.account_tenure_months,
        user_name=clean_text(payload.user_name) if payload.user_name else None,
        owner_id=owner,
    )
    emitted = _maybe_emit_spike(case, payload.prior_monthly_bill)
    if emitted and payload.prior_monthly_bill:
        user_message = (f"Whoa — your {case['provider']} bill jumped from "
                        f"{_money(payload.prior_monthly_bill)} to {_money(payload.current_monthly_bill)}. "
                        "I've logged it and your script pack is ready. One tap to see it.")
    else:
        user_message = (f"Case opened for your {case['provider']} {case['service_type']} bill "
                        f"({_money(case['current_monthly_bill'])}/mo). Your script pack is ready — "
                        "one tap and you're calling with a plan.")
    return {"case_id": case["id"], "event_emitted": emitted,
            "user_message": user_message, **serialize(case)}


@app.get("/api/cases/{cid}")
def api_get_case(cid: str):
    owner = require_owner()
    case = _owned_case(cid, owner)
    if case["new_monthly_bill"] is not None:
        user_message = (f"Your {case['provider']} case: bill went from "
                        f"{_money(case['current_monthly_bill'])} to {_money(case['new_monthly_bill'])}/mo — "
                        f"{_money(case['savings'] or 0)} in documented savings. Status: {case['billing_status']}.")
    else:
        user_message = (f"Your {case['provider']} case is open — script ready whenever you are. "
                        "Make the call, then tell me the new bill and I'll do the math.")
    return {"user_message": user_message, **serialize(case)}


@app.get("/api/cases/{cid}/script")
def api_script(cid: str):
    owner = require_owner()
    case = _owned_case(cid, owner)
    script = script_pack(case["provider"], case["service_type"],
                         case["current_monthly_bill"], case["account_tenure_months"])
    user_message = (f"Your {script['provider']} script is ready. Contact the provider yourself, describe only changes that actually occurred, and ask about available options. Confirm total costs and terms before accepting an offer.")
    return {"case_id": cid, "user_message": user_message, "script": script}


@app.post("/api/cases/{cid}/outcome")
def api_outcome(cid: str, payload: OutcomeIn):
    """User reports the negotiation result. Computes documented savings:
    savings = (old bill - new bill) x months_locked, months capped at 12."""
    owner = require_owner()
    case = _owned_case(cid, owner)
    if case.get("fee_confirmed_at") or case.get("billing_status") in ("fee_charged", "fee_processing"):
        raise HTTPException(409, "A billed outcome cannot be changed.")
    if payload.success and (payload.new_monthly_bill is None or payload.months_locked is None):
        raise HTTPException(422, "A successful outcome requires new_monthly_bill and months_locked.")
    old = case["current_monthly_bill"]
    if not payload.success or payload.new_monthly_bill is None:
        if not db.update_outcome(cid, owner, new_monthly_bill=None, months_locked=None, savings=0.0,
                    outcome_reported_at=datetime.now(timezone.utc).isoformat()):
            raise HTTPException(409, "This outcome was locked by an approved fee.")
        return {"case_id": cid, "success": False,
                "user_message": "No savings recorded, so no fee is owed. "
                                "You can return with a different offer or bill.",
                **serialize(get_case(cid, owner))}
    new = payload.new_monthly_bill
    if new >= old:
        raise HTTPException(400, "new_monthly_bill must be lower than the current bill "
                                 f"({_money(old)}) to count as savings")
    months = min(payload.months_locked or 1, 12)
    savings = round((old - new) * months, 2)
    if not db.update_outcome(cid, owner, new_monthly_bill=new, months_locked=months, savings=savings,
                outcome_reported_at=datetime.now(timezone.utc).isoformat()):
        raise HTTPException(409, "This outcome was locked by an approved fee.")
    fee = savings * FEE_RATE
    user_message = (f"That's {_money(savings)} in documented savings over {months} months — "
                    f"from {_money(old)} down to {_money(new)}/mo. My fee would be {_money(fee)} "
                    "(35%), and only if you confirm this bill.")
    return {"case_id": cid, "success": True,
            "old_monthly_bill": old, "new_monthly_bill": new,
            "months_locked": months,
            "monthly_saving": round(old - new, 2),
            "documented_savings": savings,
            "estimated_fee_cents": fee_cents(savings),
            "estimated_fee_rate": FEE_RATE,
            "user_message": user_message,
            **serialize(get_case(cid, owner))}


@app.post("/api/cases/{cid}/billing/setup")
def api_billing_setup(cid: str, consent: BillingConsentIn):
    """Open Stripe-hosted card setup after the user accepts the disclosed fee. No charge."""
    owner = require_owner()
    case = _owned_case(cid, owner)
    if not case:
        raise HTTPException(404, "Unknown case")
    if case.get("billing_status") == "fee_charged":
        raise HTTPException(409, "The fee is already settled.")
    setup_key = f"{CONNECTOR}-setup-{owner}-{cid}"
    customer_id = case.get("stripe_customer_id")
    if not customer_id:
        customer = setup_customer(case.get("user_name") or "BillCut customer", "", idempotency_key=setup_key)
        if "error" in customer:
            return _billing_failure(customer)
        customer_id = customer["customer_id"]
        update_case(cid, owner, **{"stripe_customer_id": customer_id})
    setup = create_card_setup(customer_id, idempotency_key=setup_key)
    if "error" in setup:
        return _billing_failure(setup)
    update_case(cid, owner, **{"setup_intent_id": setup.get("setup_intent_id"), "checkout_session_id": setup.get("checkout_session_id"), "billing_status": "card_pending", "fee_terms_accepted_at": datetime.now(timezone.utc).isoformat()})
    return {"case_id": cid, "stripe_customer_id": customer_id,
            "setup_url": setup.get("setup_url"), "checkout_session_id": setup.get("checkout_session_id"),
            "setup_intent_id": setup.get("setup_intent_id"), "fee_disclosure": FEE_DISCLOSURE,
            "user_message": FEE_DISCLOSURE + " Open setup_url to save a payment method securely with Stripe. Saving it does not charge a fee."}


@app.get("/api/cases/{cid}/billing/status")
def api_billing_status(cid: str):
    """Verify saved-card setup with Stripe; a pending checkout is never a saved card."""
    owner = require_owner()
    case = _owned_case(cid, owner)
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


@app.post("/api/cases/{cid}/savings-confirmed")
def api_savings_confirmed(cid: str, payload: FeeConsentIn):
    """User confirms documented savings: charge the 35% fee off-session.

    The fee is re-derived server-side from the STORED documented savings —
    the request approves the exact fee quoted from that stored outcome."""
    owner = require_owner()
    case = _owned_case(cid, owner)
    if case.get("billing_status") == "fee_charged":
        raise HTTPException(400, "Fee already charged for this case")
    if case.get("new_monthly_bill") is None:
        if case.get("outcome_reported_at"):
            update_case(cid, owner, billing_status="no_fee_no_savings")
            return {"case_id": cid, "documented_savings": 0.0, "fee_cents": 0,
                    "status": "no_fee_no_savings",
                    "user_message": "No documented savings, so no charge. Nothing owed."}
        raise HTTPException(400, "No outcome reported yet: POST /api/cases/{id}/outcome first")
    savings = case.get("savings") or 0.0
    if savings <= 0:
        update_case(cid, owner, billing_status="no_fee_no_savings")
        return {"case_id": cid, "documented_savings": savings,
                "fee_cents": 0, "status": "no_fee_no_savings",
                "user_message": "No documented savings, so no charge. Nothing owed."}
    customer_id = case.get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(400, "Billing not set up: call /billing/setup and save a card first")
    cents = fee_cents(savings)
    if payload.fee_amount_cents != cents:
        raise HTTPException(409, {"error": "fee_changed", "expected_fee_amount_cents": cents, "user_message": "Review the current fee and explicitly approve that amount."})
    if not case.get("fee_terms_accepted_at"):
        raise HTTPException(409, "Accept the fee terms through billing setup first.")
    if not db.reserve_fee(cid, owner, case["outcome_reported_at"], datetime.now(timezone.utc).isoformat()):
        raise HTTPException(409, "The outcome changed. Review the updated fee before approving it.")
    result = charge_fee(customer_id, cents,
                        f"Bill negotiation fee ({int(FEE_RATE*100)}% of {_money(savings)} documented savings) - case {cid}",
                        idempotency_key=f"{CONNECTOR}-fee-{owner}-{cid}",
        setup_intent_id=case.get("setup_intent_id"),
        checkout_session_id=case.get("checkout_session_id"), consent=payload.confirm_fee)
    if result.get("status") != "succeeded" and "error" not in result:
        result = {**result, "error": "Payment has not succeeded.", "code": "payment_pending"}
    if "error" in result:
        update_case(cid, owner, billing_status="fee_failed")
        return _billing_failure(result)
    update_case(cid, owner, billing_status="fee_charged", fee_cents=cents,
                payment_intent_id=result["payment_intent_id"])
    return {"case_id": cid, "documented_savings": savings,
            "fee_rate": FEE_RATE, "fee_cents": cents,
            "payment_intent_id": result["payment_intent_id"],
            "status": "fee_charged",
            "user_message": f"Done — {_money(cents / 100)} charged (35% of {_money(savings)} in savings). "
                            "That's money back in your pocket every month from here on."}


@app.post("/api/life-events")
def api_life_events(event: LifeEventIn):
    """Receive fan-out from the shared life-events bus. For bill_spike: create a
    draft case (pre-fill every field the payload provides), return the proactive
    nudge as user_message plus the ready-to-use script pack — trigger → one tap → done."""
    if event.event_type != "bill_spike":
        return {"received": True, "handled": False,
                "user_message": f"Got a '{clean_text(event.event_type, max_len=40)}' event — "
                                "nothing for me to do with it, so I'm standing by."}
    owner = require_owner()
    intake = _validate_input(CaseIn, event.payload or {})
    p = intake.model_dump()
    provider, service_type = intake.provider, intake.service_type
    current, prior = intake.current_monthly_bill, intake.prior_monthly_bill
    case = create_case(provider=provider, service_type=service_type,
                       current_monthly_bill=current, promo_end_date=intake.promo_end_date.isoformat() if intake.promo_end_date else None,
                       account_tenure_months=intake.account_tenure_months,
                       user_name=intake.user_name, owner_id=owner)
    script = script_pack(provider, service_type, current, case["account_tenure_months"])
    return {"received": True, "handled": True, "event_type": "bill_spike",
            "case_id": case["id"],
            "user_message": bill_spike_nudge(provider, service_type, prior, current),
            "script": script, **serialize(case)}


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


@app.get("/api/cases/{cid}/billing/quote")
def quote_fee(cid: str):
    """Quote the exact fee for the recorded outcome; does not charge or confirm it."""
    case = _owned_case(cid, require_owner())
    if not case.get("outcome_reported_at"):
        raise HTTPException(409, "Report the negotiation outcome first")
    savings = case.get("savings") or 0
    fee = fee_cents(savings) if savings > 0 else 0
    return {"case_id": cid, "currency": "USD", "documented_savings": savings,
            "months_locked": case.get("months_locked"), "fee_rate": FEE_RATE,
            "fee_amount_cents": fee, "charged": False,
            "user_message": f"The fee would be ${fee / 100:.2f}, based on the recorded savings and capped at 12 months. Review and approve the exact amount before payment."}


# Shared authenticated API schema and payment return page.
try:
    from api_support import install_api_contract
except ImportError:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from api_support import install_api_contract
install_api_contract(app, "bill-negotiator", app.title)
