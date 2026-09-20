"""eu261-flight-comp connector API — agent-native design.

The agent is the UI: every response carries a `user_message` field — a warm,
ready-to-speak sentence the agent can say verbatim. Every flow is demoable in a
plain chat transcript.

Security notes:
  - All inputs are validated with pydantic models.
  - All user-supplied text is treated as untrusted data and escaped before
    being rendered into PDFs (see src/pack.py). It never alters queries or
    instructions — there are no prompt-injection-vulnerable surfaces.
  - Stripe calls use the shared SDK billing adapter with owner-bound hosted
    setup and explicit fee consent. Unit/integration tests mock the provider.
"""
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ConfigDict, ValidationError, BaseModel, EmailStr, Field, field_validator, model_validator

from src import db
from src import billing
from src import eligibility
from src import pack
from src.identity import IdentityMiddleware, IdentityError, require_owner
from src.limits import RateLimitMiddleware, BodySizeLimitMiddleware

life_events = None  # Cross-service delivery requires a verified owner-scoped transport.
# import events as life_events

_PROD = os.environ.get("ENV") == "production"
_PUBLIC_DOCS = os.environ.get("PUBLIC_DOCS") == "1"

app = FastAPI(
    title="EU261 Flight Compensation API",
    version="0.1.0",
    docs_url="/docs" if (_PUBLIC_DOCS or not _PROD) else None,
    redoc_url="/redoc" if (_PUBLIC_DOCS or not _PROD) else None,
    openapi_url="/openapi.json" if (_PUBLIC_DOCS or not _PROD) else None,
)

# Added last runs first: identity is checked before rate/body limits.
app.add_middleware(IdentityMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(BodySizeLimitMiddleware)


@app.exception_handler(IdentityError)
async def _identity_exc(request: Request, exc: IdentityError):
    return JSONResponse(
        status_code=401,
        content={"detail": str(exc), "user_message": str(exc)},
    )

PACK_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent / "data")) / "claim_packs"
PACK_DIR.mkdir(parents=True, exist_ok=True)

FEE_DISCLOSURE = (
    "You will be charged 30% of the confirmed EU261 payout, only if you confirm "
    "the payout. No charge otherwise. You can cancel at any time before confirming."
)

CITY_OF = {"CDG": "Paris", "ORY": "Paris", "LHR": "London", "JFK": "New York",
           "FCO": "Rome", "MAD": "Madrid", "BCN": "Barcelona", "AMS": "Amsterdam",
           "FRA": "Frankfurt", "BER": "Berlin", "SXF": "Berlin", "TXL": "Berlin",
           "LIS": "Lisbon", "ATH": "Athens", "DUB": "Dublin", "ZRH": "Zurich",
           "VIE": "Vienna", "PRG": "Prague", "WAW": "Warsaw", "CPH": "Copenhagen",
           "ARN": "Stockholm", "OSL": "Oslo", "HEL": "Helsinki"}


def _city(iata: str) -> str:
    ap = eligibility.lookup_airport(iata)
    if ap:
        return ap["city"]
    return CITY_OF.get(iata.upper(), iata.upper())


def _primary_reason(verdict: dict) -> str:
    """Pick the most decision-relevant reason for a speakable summary."""
    reasons = verdict.get("reasons") or []
    for r in reasons:
        low = r.lower()
        if any(k in low for k in ("out of scope", "ineligible", "< 3h", "no fixed compensation",
                                  "no compensation", "could not determine", "missing ",
                                  "unknown disruption")):
            return r
    return reasons[0] if reasons else "I couldn't assess this flight."


def speak_verdict(verdict: dict, payload: dict) -> str:
    """Warm, speakable summary of a verdict for the agent to say verbatim."""
    if verdict.get("assessment") == "needs_review":
        return "This flight needs more information or manual review before eligibility can be estimated: " + "; ".join(verdict["reasons"])
    if verdict["eligible"]:
        dest = _city(verdict["to"]["iata"])
        amt = verdict["compensation_eur"]
        return (f"Based on the information supplied, your flight to {dest} may qualify for €{amt}. The airline must assess the claim and exceptions. I can prepare a letter for you to review and send.")
    return (f"The information supplied does not establish compensation eligibility: {_primary_reason(verdict)} Happy to double-check the details "
            f"if anything looks off.")


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


class ClaimIn(StrictModel):
    passenger_name: str = Field(..., min_length=1, max_length=120)
    passenger_email: EmailStr
    flight_number: str = Field(..., min_length=2, max_length=10)
    flight_date: str = Field(..., description="Flight date, YYYY-MM-DD")
    airline: str = Field(..., min_length=1, max_length=120)
    airline_eu_licensed: bool = Field(
        False, description="True if the carrier is licensed in the EU/EEA")
    from_iata: str = Field(..., min_length=3, max_length=3, description="Departure airport IATA code")
    to_iata: str = Field(..., min_length=3, max_length=3, description="Arrival airport IATA code")
    distance_km: float | None = Field(
        None, gt=0, description="Great-circle distance in km; used only if an airport code is unknown")
    disruption: Literal["delay", "cancellation", "denied_boarding"]
    scheduled_arrival: str | None = Field(None, description="Scheduled arrival, ISO datetime")
    actual_arrival: str | None = Field(None, description="Actual arrival, ISO datetime")
    arrival_delay_h: float | None = Field(None, ge=0, description="Arrival delay in hours")
    notice_days: float | None = Field(None, ge=0, description="Cancellation notice, days before departure")
    rerouted: bool = False
    reroute_dep_early_h: float | None = Field(None, ge=0)
    reroute_arr_delay_h: float | None = Field(None, ge=0)
    denied_boarding_involuntary: bool | None = None
    valid_travel_documents: bool | None = None
    extraordinary_circumstances: bool = Field(
        False, description="True if the airline claims extraordinary circumstances")

    @field_validator("from_iata", "to_iata", "flight_number")
    @classmethod
    def upper_strip(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("flight_date")
    @classmethod
    def valid_date(cls, v: str) -> str:
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError("flight_date must be YYYY-MM-DD")
        return v

    @model_validator(mode="after")
    def check_route_and_delay(self):
        if not eligibility.lookup_airport(self.from_iata) and not self.distance_km:
            raise ValueError(f"Unknown departure airport {self.from_iata}; provide distance_km")
        if not eligibility.lookup_airport(self.to_iata) and not self.distance_km:
            raise ValueError(f"Unknown arrival airport {self.to_iata}; provide distance_km")
        if self.from_iata == self.to_iata:
            raise ValueError("Departure and arrival must differ")
        if self.disruption == "delay" and self.arrival_delay_h is None:
            for dt in (self.scheduled_arrival, self.actual_arrival):
                if dt is None:
                    raise ValueError("For a delay, provide arrival_delay_h or both "
                                     "scheduled_arrival and actual_arrival")
                try:
                    datetime.fromisoformat(dt)
                except ValueError:
                    raise ValueError("scheduled_arrival/actual_arrival must be ISO datetimes")
            parsed = [datetime.fromisoformat(dt) for dt in (self.scheduled_arrival, self.actual_arrival)]
            if any(dt.tzinfo is None for dt in parsed):
                raise ValueError("Arrival datetimes require explicit timezone offsets")
            if parsed[1] < parsed[0]:
                raise ValueError("Actual arrival cannot precede scheduled arrival for a delay claim")
        return self


class LifeEventIn(StrictModel):
    event_type: str = Field(..., min_length=1, max_length=64)
    payload: dict = Field(default_factory=dict)


def _payload_with_computed_delay(data: dict) -> dict:
    if data.get("disruption") == "delay" and data.get("arrival_delay_h") is None:
        sched, actual = data.get("scheduled_arrival"), data.get("actual_arrival")
        if sched and actual:
            s = datetime.fromisoformat(sched)
            a = datetime.fromisoformat(actual)
            data["arrival_delay_h"] = max(0.0, (a - s).total_seconds() / 3600)
    return data


def _nudge_for_draft(payload: dict, verdict: dict) -> str:
    """The proactive nudge: warm, specific, realistic numbers, one clear ask."""
    dest = _city(payload.get("to_iata", ""))
    flight = payload.get("flight_number", "your flight")
    if verdict["eligible"]:
        amt = verdict["compensation_eur"]
        delay = payload.get("arrival_delay_h")
        delay_txt = f"landed {delay:.0f} hours late" if delay else "was disrupted"
        return (f"Your flight {flight} to {dest} {delay_txt}. EU law says that's "
                f"€{amt} back in your pocket. Want me to draft the claim letter? One tap.")
    missing = [k for k in ("passenger_name", "passenger_email", "flight_number",
                           "from_iata", "to_iata")
               if not payload.get(k)]
    if missing:
        pretty = {"passenger_name": "your name", "passenger_email": "your email",
                  "flight_number": "the flight number", "from_iata": "where you flew from",
                  "to_iata": "where you were headed"}[missing[0]]
        return (f"Heads up — flight {flight} to {dest} looks like it might qualify for EU "
                f"compensation. What's {pretty}, so I can check it properly?")
    return speak_verdict(verdict, payload)


def _card_state(claim: dict) -> str:
    """Pollable card-save state for GET .../billing/status."""
    if not claim.get("stripe_customer_id"):
        return "none"
    return {"card_pending": "pending", "fee_charged": "ready",
            "fee_failed": "failed"}.get(claim.get("billing_status") or "", "none")


@app.get("/health")
def health():
    return {"ok": True, "service": "eu261-flight-comp", "version": "0.1.0",
            "user_message": "EU261 flight-compensation service is up and ready."}


@app.post("/api/life-events")
def life_events_in(event: LifeEventIn):
    """Receive a fanned-out life event. Creates a draft claim and returns the nudge.

    Each draft is owned by the caller resolved from their verified API key.

    Golden path: trigger -> one tap -> done. The payload pre-fills the draft;
    anything missing is asked for conversationally, one question at a time.
    """
    if event.event_type != "flight_delayed":
        return {"accepted": False, "event_type": event.event_type,
                "user_message": f"I don't handle '{event.event_type}' events — nothing to do here."}
    owner = require_owner()
    allowed = set(ClaimIn.model_fields)
    if set(event.payload) - allowed:
        raise HTTPException(422, "Unknown flight fields")
    raw = dict(event.payload or {})
    from pydantic import TypeAdapter
    for key, value in raw.items():
        try:
            raw[key] = TypeAdapter(ClaimIn.model_fields[key].rebuild_annotation()).validate_python(value)
        except ValidationError:
            raise HTTPException(422, f"Invalid flight field: {key}")
    for key in ("arrival_delay_h", "notice_days", "reroute_dep_early_h", "reroute_arr_delay_h", "distance_km"):
        if raw.get(key) is not None and (not __import__("math").isfinite(float(raw[key])) or float(raw[key]) < 0):
            raise HTTPException(422, f"Invalid nonnegative number: {key}")
    for k in ("from_iata", "to_iata", "flight_number"):
        if raw.get(k):
            raw[k] = str(raw[k]).strip().upper()
    raw.setdefault("disruption", "delay")
    try:
        raw = _payload_with_computed_delay(raw)
    except (ValueError, TypeError):
        raise HTTPException(422, "Arrival timestamps must have matching timezone offsets and valid ISO format")
    cid = db.create_claim({**raw, "_draft": True}, owner_id=owner)
    verdict = eligibility.verdict(raw)
    nudge = _nudge_for_draft(raw, verdict)
    return {"accepted": True, "claim_id": cid, "draft": True,
            "eligible": verdict["eligible"],
            "compensation_eur": verdict["compensation_eur"],
            "user_message": nudge}


@app.post("/api/claims", status_code=201)
def create_claim(payload: ClaimIn):
    """Intake a flight disruption and return the EU261 eligibility verdict."""
    owner = require_owner()
    data = _payload_with_computed_delay(payload.model_dump())
    cid = db.create_claim(data, owner_id=owner)
    verdict = eligibility.verdict(data)
    return {"claim_id": cid, **verdict, "user_message": speak_verdict(verdict, data)}


@app.get("/api/claims/{cid}")
def get_claim(cid: str):
    """Eligibility verdict + tier + amount for a stored claim.

    Also exposes the money state (billing_status, payout/fee amounts,
    payment_intent_id) so the agent can poll the charge after confirming.
    """
    owner = require_owner()
    claim = db.get_claim(cid, owner_id=owner)
    if not claim:
        raise HTTPException(404, "Unknown claim")
    verdict = eligibility.verdict(claim["payload"])
    return {"claim_id": cid, **verdict,
            "billing_status": claim.get("billing_status") or "none",
            "payout_amount_eur": claim.get("payout_amount_eur"),
            "fee_amount_eur": claim.get("fee_amount_eur"),
            "payment_intent_id": claim.get("payment_intent_id"),
            "user_message": speak_verdict(verdict, claim["payload"])}


@app.post("/api/claims/{cid}/claim-pack")
def claim_pack(cid: str):
    """Generate the claim letter/form pack PDF. Only available when eligible."""
    owner = require_owner()
    claim = db.get_claim(cid, owner_id=owner)
    if not claim:
        raise HTTPException(404, "Unknown claim")
    verdict = eligibility.verdict(claim["payload"])
    if not verdict["eligible"]:
        raise HTTPException(400, "Claim pack is only generated for eligible claims. "
                                 "Ask me why this one doesn't qualify and I'll explain.")
    _validate_input(ClaimIn, {k: v for k, v in claim["payload"].items() if k in ClaimIn.model_fields})
    path = PACK_DIR / f"eu261-claim-pack-{cid}.pdf"
    pack.build_pdf(claim["payload"], verdict, str(path))
    dest = _city(verdict["to"]["iata"])
    return {"claim_id": cid, "generated": True,
            "pdf": f"eu261-claim-pack-{cid}.pdf",
            "pdf_base64": __import__("base64").b64encode(path.read_bytes()).decode(),
            "download_path": f"/api/claims/{cid}/claim-pack.pdf",
            "user_message": (f"Done — your €{verdict['compensation_eur']} claim letter for the "
                             f"{dest} flight is ready. Just sign it and send it to the airline; "
                             f"their claims email is usually on their website.")}


@app.post("/api/claims/{cid}/billing/setup")
def billing_setup(cid: str, consent: BillingConsentIn):
    """Open Stripe-hosted card setup after the user accepts the disclosed fee. No charge."""
    owner = require_owner()
    claim = db.get_claim(cid, owner_id=owner)
    if not claim:
        raise HTTPException(404, "Unknown claim")
    if claim.get("billing_status") == "fee_charged":
        raise HTTPException(409, "The fee is already settled.")
    setup_key = f"{billing.CONNECTOR}-setup-{owner}-{cid}"
    customer_id = claim.get("stripe_customer_id")
    if not customer_id:
        customer = billing.setup_customer(claim["payload"].get("passenger_name", ""), claim["payload"].get("passenger_email", ""), idempotency_key=setup_key)
        if "error" in customer:
            return _billing_failure(customer)
        customer_id = customer["customer_id"]
        db.update_claim(cid, owner_id=owner, **{"stripe_customer_id": customer_id})
    setup = billing.create_card_setup(customer_id, idempotency_key=setup_key)
    if "error" in setup:
        return _billing_failure(setup)
    db.update_claim(cid, owner_id=owner, **{"setup_intent_id": setup.get("setup_intent_id"), "checkout_session_id": setup.get("checkout_session_id"), "billing_status": "card_pending", "fee_terms_accepted_at": datetime.now(timezone.utc).isoformat()})
    return {"claim_id": cid, "stripe_customer_id": customer_id,
            "setup_url": setup.get("setup_url"), "checkout_session_id": setup.get("checkout_session_id"),
            "setup_intent_id": setup.get("setup_intent_id"), "fee_disclosure": FEE_DISCLOSURE,
            "user_message": FEE_DISCLOSURE + " Open setup_url to save a payment method securely with Stripe. Saving it does not charge a fee."}


@app.get("/api/claims/{cid}/billing/status")
def billing_status(cid: str):
    """Verify saved-card setup with Stripe; a pending checkout is never a saved card."""
    owner = require_owner()
    claim = db.get_claim(cid, owner_id=owner)
    if not claim:
        raise HTTPException(404, "Unknown claim")
    verified = {"status": "none"}
    if claim.get("stripe_customer_id"):
        verified = billing.retrieve_card_setup(claim["stripe_customer_id"],
            setup_intent_id=claim.get("setup_intent_id"),
            checkout_session_id=claim.get("checkout_session_id"))
    state = "ready" if verified.get("status") == "succeeded" and "error" not in verified else "none" if not claim.get("stripe_customer_id") else "pending"
    return {"claim_id": cid, "billing_status": claim.get("billing_status") or "none",
            "card_state": state, "setup_status": verified.get("status"),
            "fee_disclosure": FEE_DISCLOSURE,
            "user_message": "Your payment method is saved. Review and explicitly approve the fee before payment." if state == "ready" else "Card setup has not been confirmed. Open the Stripe setup link and complete it."}


class PayoutIn(FeeConsentIn):
    amount_eur: float = Field(..., gt=0, le=1_000_000, description="Airline payout the user confirms they received")


@app.post("/api/claims/{cid}/payout-confirmed")
def payout_confirmed(cid: str, payload: PayoutIn):
    """User confirms the airline paid out: charge the 30% contingency fee off-session.

    Quote and approve 30% of the actual EUR payout. Partial payouts are
    accepted up to the stored preliminary compensation tier; larger amounts
    require review. The exact fee amount and affirmative approval are required.
    """
    owner = require_owner()
    claim = db.get_claim(cid, owner_id=owner)
    if not claim:
        raise HTTPException(404, "Unknown claim")
    if not claim.get("stripe_customer_id"):
        raise HTTPException(400, "Set up billing first via POST /api/claims/{id}/billing/setup")
    if claim.get("billing_status") == "fee_charged":
        raise HTTPException(400, "Fee already charged for this claim")
    stored_tier = eligibility.verdict(claim["payload"])["compensation_eur"]
    if stored_tier <= 0 or payload.amount_eur > stored_tier:
        raise HTTPException(
            400, f"That doesn't add up — you confirmed €{payload.amount_eur:.2f}, but this "
                 f"claim's preliminary EU261 tier is €{stored_tier}. The fee is 30% of the actual received payout. "
                 "Mind double-checking what the airline actually paid?")
    stored_tier = payload.amount_eur
    cents = billing.fee_cents(stored_tier)
    if payload.fee_amount_cents != cents:
        raise HTTPException(409, {"error": "fee_changed", "expected_fee_amount_cents": cents, "user_message": "Review the current fee and explicitly approve that amount."})
    if not claim.get("fee_terms_accepted_at"):
        raise HTTPException(409, "Accept the fee terms through billing setup first.")
    if not db.reserve_fee(cid, owner, claim["payload"], datetime.now(timezone.utc).isoformat()):
        raise HTTPException(409, "The claim changed. Review the updated fee before approving it.")
    res = billing.charge_fee(
        claim["stripe_customer_id"], cents,
        f"EU261 contingency fee (30%) on EUR {stored_tier:.2f} payout, claim {cid}",
        idempotency_key=f"{billing.CONNECTOR}-fee-{owner}-{cid}",
        setup_intent_id=claim.get("setup_intent_id"),
        checkout_session_id=claim.get("checkout_session_id"), consent=payload.confirm_fee)
    if res.get("status") != "succeeded" and "error" not in res:
        res = {**res, "error": "Payment has not succeeded.", "code": "payment_pending"}
    if "error" in res:
        db.update_claim(cid, owner_id=owner, billing_status="fee_failed")
        return _billing_failure(res)
    db.update_claim(cid, owner_id=owner, billing_status="fee_charged",
                    payout_amount_eur=stored_tier,
                    fee_amount_eur=cents / 100,
                    payment_intent_id=res["payment_intent_id"])
    fee = round(cents / 100, 2)
    return {"claim_id": cid, "payout_eur": stored_tier,
            "fee_eur": fee, "fee_rate": billing.FEE_RATE,
            "payment_intent_id": res["payment_intent_id"],
            "status": res["status"],
            "user_message": (f"Confirmed — the airline paid €{stored_tier:.2f}, so our "
                             f"30% fee of €{fee:.2f} has been charged. Congrats on the win!")}


@app.get("/api/claims/{cid}/claim-pack.pdf")
def download_claim_pack(cid: str):
    claim = db.get_claim(cid, owner_id=require_owner())
    if not claim:
        raise HTTPException(404, "Unknown claim")
    path = PACK_DIR / f"eu261-claim-pack-{cid}.pdf"
    if not path.is_file():
        raise HTTPException(404, "Generate the claim pack first")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@app.post("/api/claims/{cid}/confirm")
def confirm_claim(cid: str, payload: ClaimIn):
    owner = require_owner()
    claim = db.get_claim(cid, owner_id=owner)
    if not claim:
        raise HTTPException(404, "Unknown claim")
    if claim.get("fee_confirmed_at"):
        raise HTTPException(409, "A claim cannot change after its fee has been approved.")
    data = _payload_with_computed_delay(payload.model_dump())
    if not db.complete_claim(cid, owner, data):
        raise HTTPException(409, "The claim was locked by an approved fee.")
    (PACK_DIR / f"eu261-claim-pack-{cid}.pdf").unlink(missing_ok=True)
    verdict = eligibility.verdict(data)
    return {"claim_id": cid, **verdict, "user_message": speak_verdict(verdict, data)}


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
    for row in deleted.get("claims", []):
        (PACK_DIR / f"eu261-claim-pack-{row['id']}.pdf").unlink(missing_ok=True)
    return {"deleted": {table: len(rows) for table, rows in deleted.items()},
            "user_message": "Your local records and generated documents have been deleted. Stripe transaction records and the payment audit ledger are retained separately; contact support for related requests."}


class FeeQuoteIn(StrictModel):
    amount_eur: float = Field(gt=0, le=1_000_000, description="The actual recovered amount to quote; this operation does not charge.")


@app.post("/api/claims/{cid}/billing/quote")
def quote_fee(cid: str, payload: FeeQuoteIn):
    """Show the exact rounded fee for review; no card setup, confirmation or payment occurs."""
    owner = require_owner()
    claim = db.get_claim(cid, owner_id=owner)
    if not claim:
        raise HTTPException(404, "Unknown claim")
    if payload.amount_eur > eligibility.verdict(claim["payload"])["compensation_eur"]:
        raise HTTPException(400, "The recovery amount exceeds the recorded case amount.")
    fee = billing.fee_cents(payload.amount_eur)
    return {"claim_id": cid, "currency": "EUR", "recovery_amount": payload.amount_eur,
            "fee_rate": 0.30, "fee_amount_cents": fee, "charged": False,
            "user_message": f"The fee would be {fee / 100:.2f} EUR. Review it, then explicitly approve this exact amount if you want to pay."}


# Shared authenticated API schema and payment return page.
try:
    from api_support import install_api_contract
except ImportError:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from api_support import install_api_contract
install_api_contract(app, "eu261-flight-comp", app.title)
