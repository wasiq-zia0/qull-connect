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
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.db import init_db, create_case, get_case, update_case, claim_case, serialize
from src.scripts import script_pack, list_providers, clean_text
from src.billing import (setup_customer, create_card_setup, charge_fee, fee_cents,
                         FEE_RATE, CONNECTOR)
from src.identity import IdentityMiddleware, IdentityError, require_owner
from src.limits import RateLimitMiddleware, BodySizeLimitMiddleware

# Shared life-events bus (emit what we detect; receive fan-out on /api/life-events).
LIFE_EVENTS_DIR = Path.home() / "workspace/connectors/life-events"
if str(LIFE_EVENTS_DIR) not in sys.path:
    sys.path.insert(0, str(LIFE_EVENTS_DIR))
try:
    import events as life_events
except Exception:  # bus optional at runtime; receiving still works
    life_events = None

_PROD = os.environ.get("ENV") == "production"
_PUBLIC_DOCS = os.environ.get("PUBLIC_DOCS") == "1"
app = FastAPI(title="Bill Negotiator API", version="0.2.0",
              docs_url="/docs" if (_PUBLIC_DOCS or not _PROD) else None,
              redoc_url="/redoc" if (_PUBLIC_DOCS or not _PROD) else None,
              openapi_url="/openapi.json" if (_PUBLIC_DOCS or not _PROD) else None)

# Middleware: added last runs first, so IdentityMiddleware enforces identity
# before rate limiting or body-size checks see the request.
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(IdentityMiddleware)

init_db()


@app.exception_handler(IdentityError)
async def _identity_error(request: Request, exc: IdentityError):
    return JSONResponse(status_code=401, content={
        "ok": False, "error": "unauthorized",
        "user_message": str(exc)})

FEE_DISCLOSURE = ("You will be charged 35% of your documented bill savings, only if you confirm "
                  "the new lower bill. No charge otherwise.")


# ---------- schemas ----------

class CaseIn(BaseModel):
    provider: str = Field(..., min_length=2, description="e.g. Comcast, Spectrum, AT&T, Verizon, Cox")
    service_type: str = Field(..., description="internet, cable, phone, or bundle")
    current_monthly_bill: float = Field(..., gt=0, description="Current bill in USD/month")
    prior_monthly_bill: float | None = Field(None, gt=0, description="What you used to pay (optional; enables spike detection)")
    promo_end_date: str | None = Field(None, description="Contract/promotion end date (YYYY-MM-DD)")
    account_tenure_months: int | None = Field(None, ge=0, description="How long you've been a customer")
    user_name: str | None = Field(None, min_length=1)


class OutcomeIn(BaseModel):
    success: bool = Field(True, description="False if negotiation produced no savings")
    new_monthly_bill: float | None = Field(None, gt=0, description="Negotiated monthly bill in USD")
    months_locked: int | None = Field(None, ge=1, le=36, description="Months the new rate is locked")


class LifeEventIn(BaseModel):
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
    """Emit bill_spike on the shared bus when we detect one. Returns the event type or None."""
    if life_events is None or not prior:
        return None
    if case["current_monthly_bill"] <= prior:
        return None
    try:
        life_events.emit("bill_spike", {
            "provider": case["provider"],
            "service_type": case["service_type"],
            "prior_monthly_bill": prior,
            "current_monthly_bill": case["current_monthly_bill"],
            "case_id": case["id"],
        }, source="bill-negotiator")
        return "bill_spike"
    except Exception:
        return None


def bill_spike_nudge(provider: str, service_type: str,
                     prior: float | None, current: float) -> str:
    if prior and current and current > prior:
        return (f"Your {service_type} bill jumped from {_money(prior)} to {_money(current)} — "
                "your promo expired. People are getting it back down with one call. "
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
        promo_end_date=payload.promo_end_date,
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
    user_message = (f"Your {script['provider']} game plan is ready: call the retention team, "
                    "say your promo ended, mention a competitor's price, and ask them to match it. "
                    "The exact words are in the script — you've got this.")
    return {"case_id": cid, "user_message": user_message, "script": script}


@app.post("/api/cases/{cid}/outcome")
def api_outcome(cid: str, payload: OutcomeIn):
    """User reports the negotiation result. Computes documented savings:
    savings = (old bill - new bill) x months_locked, months capped at 12."""
    owner = require_owner()
    case = _owned_case(cid, owner)
    old = case["current_monthly_bill"]
    if not payload.success or payload.new_monthly_bill is None:
        update_case(cid, owner, new_monthly_bill=None, months_locked=None, savings=0.0,
                    outcome_reported_at=datetime.now(timezone.utc).isoformat())
        return {"case_id": cid, "success": False,
                "user_message": "No luck this time — and no fee owed. I'll keep an eye out "
                                "for your next promo window so we can try again.",
                **serialize(get_case(cid, owner))}
    new = payload.new_monthly_bill
    if new >= old:
        raise HTTPException(400, "new_monthly_bill must be lower than the current bill "
                                 f"({_money(old)}) to count as savings")
    months = min(payload.months_locked or 1, 12)
    savings = round((old - new) * months, 2)
    update_case(cid, owner, new_monthly_bill=new, months_locked=months, savings=savings,
                outcome_reported_at=datetime.now(timezone.utc).isoformat())
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
def api_billing_setup(cid: str):
    """Create the Stripe customer and a card-setup intent for the contingency fee."""
    owner = require_owner()
    case = _owned_case(cid, owner)
    if case.get("stripe_customer_id"):
        raise HTTPException(400, "Billing already set up for this case")
    name = clean_text(case.get("user_name") or f"Bill negotiation case {cid}")
    setup_key = f"{CONNECTOR}-setup-{owner}-{cid}"
    cust = setup_customer(name, idempotency_key=setup_key)
    if "error" in cust:
        raise HTTPException(502, f"Stripe customer creation failed: {cust['error']}")
    setup = create_card_setup(cust["customer_id"], idempotency_key=setup_key)
    if "error" in setup:
        raise HTTPException(502, f"Stripe card setup failed: {setup['error']}")
    update_case(cid, owner, stripe_customer_id=cust["customer_id"],
                setup_intent_id=setup.get("setup_intent_id"),
                billing_status="card_pending")
    return {"case_id": cid, "stripe_customer_id": cust["customer_id"],
            "client_secret": setup["client_secret"],
            "fee_disclosure": FEE_DISCLOSURE,
            "user_message": FEE_DISCLOSURE + " Save your card to continue — nothing is charged now.",
            "note": "Collect the user's card against this SetupIntent. The fee is charged "
                    "off-session ONLY after the user confirms documented savings."}


@app.get("/api/cases/{cid}/billing/status")
def api_billing_status(cid: str):
    """Pollable card-save status: billing_status, derived card_state, and the
    fee disclosure — the agent's signal for whether a charge can be attempted."""
    owner = require_owner()
    case = _owned_case(cid, owner)
    return {"case_id": cid,
            "billing_status": case.get("billing_status"),
            "card_state": _card_state(case),
            "stripe_customer_id": case.get("stripe_customer_id"),
            "fee_rate": case.get("fee_rate"),
            "fee_cents": case.get("fee_cents"),
            "fee_disclosure": FEE_DISCLOSURE,
            "user_message": (
                "Your card is saved and ready — I'll only charge the 35% fee after you "
                "confirm documented savings." if _card_state(case) == "ready"
                else "No card on file yet — set up billing and save a card first."
                if _card_state(case) == "none"
                else f"Billing status: {case.get('billing_status')}. {FEE_DISCLOSURE}")}


@app.post("/api/cases/{cid}/savings-confirmed")
def api_savings_confirmed(cid: str):
    """User confirms documented savings: charge the 35% fee off-session.

    The fee is re-derived server-side from the STORED documented savings —
    the request carries no amount, so the caller cannot influence the charge."""
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
    result = charge_fee(customer_id, cents,
                        f"Bill negotiation fee ({int(FEE_RATE*100)}% of {_money(savings)} documented savings) - case {cid}",
                        idempotency_key=f"{CONNECTOR}-fee-{owner}-{cid}")
    if "error" in result:
        update_case(cid, owner, billing_status="fee_failed")
        raise HTTPException(502, f"Fee charge failed: {result['error']}")
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
    p = event.payload or {}
    provider = clean_text(str(p.get("provider") or "your provider"))
    service_type = clean_text(str(p.get("service_type") or "internet"), max_len=60)

    def _num(key):
        try:
            return float(p[key]) if p.get(key) is not None else None
        except (TypeError, ValueError):
            return None

    current = _num("current_monthly_bill") or 0.0
    prior = _num("prior_monthly_bill")
    tenure = p.get("account_tenure_months")
    try:
        tenure = int(tenure) if tenure is not None else None
    except (TypeError, ValueError):
        tenure = None

    case = create_case(provider=provider, service_type=service_type,
                       current_monthly_bill=current,
                       promo_end_date=p.get("promo_end_date"),
                       account_tenure_months=tenure,
                       user_name=clean_text(str(p.get("user_name"))) if p.get("user_name") else None,
                       owner_id=None)  # service-key call: no user identity; first
                                       # owner to present the case ID claims it via _owned_case
    script = script_pack(provider, service_type, current, case["account_tenure_months"])
    return {"received": True, "handled": True, "event_type": "bill_spike",
            "case_id": case["id"],
            "user_message": bill_spike_nudge(provider, service_type, prior, current),
            "script": script, **serialize(case)}
