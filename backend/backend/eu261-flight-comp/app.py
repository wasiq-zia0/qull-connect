"""eu261-flight-comp connector API — agent-native design.

The agent is the UI: every response carries a `user_message` field — a warm,
ready-to-speak sentence the agent can say verbatim. Every flow is demoable in a
plain chat transcript.

Security notes:
  - All inputs are validated with pydantic models.
  - All user-supplied text is treated as untrusted data and escaped before
    being rendered into PDFs (see src/pack.py). It never alters queries or
    instructions — there are no prompt-injection-vulnerable surfaces.
  - Stripe calls go through the stripe skill CLI only (never invoked in tests);
    no raw keys exist in this codebase.
"""
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from src import db
from src import billing
from src import eligibility
from src import pack
from src.identity import IdentityMiddleware, IdentityError, require_owner
from src.limits import RateLimitMiddleware, BodySizeLimitMiddleware

sys.path.insert(0, str(Path.home() / "workspace/connectors/life-events"))
import events as life_events

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
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(IdentityMiddleware)


@app.exception_handler(IdentityError)
async def _identity_exc(request: Request, exc: IdentityError):
    return JSONResponse(
        status_code=401,
        content={"detail": str(exc), "user_message": str(exc)},
    )

PACK_DIR = Path(__file__).resolve().parent / "claim_packs"
PACK_DIR.mkdir(exist_ok=True)

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
    if verdict["eligible"]:
        dest = _city(verdict["to"]["iata"])
        amt = verdict["compensation_eur"]
        return (f"Good news — your flight to {dest} qualifies under EU law for "
                f"€{amt}. I can generate your claim letter right now. Want me to?")
    return (f"I checked this one against EU flight-compensation rules and it doesn't "
            f"qualify: {_primary_reason(verdict)} Happy to double-check the details "
            f"if anything looks off.")


class ClaimIn(BaseModel):
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
        if self.disruption == "delay" and self.arrival_delay_h is None:
            for dt in (self.scheduled_arrival, self.actual_arrival):
                if dt is None:
                    raise ValueError("For a delay, provide arrival_delay_h or both "
                                     "scheduled_arrival and actual_arrival")
                try:
                    datetime.fromisoformat(dt)
                except ValueError:
                    raise ValueError("scheduled_arrival/actual_arrival must be ISO datetimes")
        return self


class LifeEventIn(BaseModel):
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

    Service-key authenticated (see IdentityMiddleware) — there is no user
    owner here, so the draft claim stores owner_id=NULL.

    Golden path: trigger -> one tap -> done. The payload pre-fills the draft;
    anything missing is asked for conversationally, one question at a time.
    """
    if event.event_type != "flight_delayed":
        return {"accepted": False, "event_type": event.event_type,
                "user_message": f"I don't handle '{event.event_type}' events — nothing to do here."}
    raw = dict(event.payload or {})
    for k in ("from_iata", "to_iata", "flight_number"):
        if raw.get(k):
            raw[k] = str(raw[k]).strip().upper()
    raw.setdefault("disruption", "delay")
    raw = _payload_with_computed_delay(raw)
    cid = db.create_claim({**raw, "_draft": True}, owner_id=None)
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
    # Emit to the shared bus so the suite acts as one intelligence.
    if verdict["eligible"]:
        life_events.emit("flight_delayed", {
            "claim_id": cid,
            "flight_number": data.get("flight_number"),
            "route": f"{data.get('from_iata')}->{data.get('to_iata')}",
            "compensation_eur": verdict["compensation_eur"],
        }, source="eu261-flight-comp")
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
    path = PACK_DIR / f"eu261-claim-pack-{cid}.pdf"
    pack.build_pdf(claim["payload"], verdict, str(path))
    dest = _city(verdict["to"]["iata"])
    return {"claim_id": cid, "generated": True,
            "pdf": f"eu261-claim-pack-{cid}.pdf",
            "user_message": (f"Done — your €{verdict['compensation_eur']} claim letter for the "
                             f"{dest} flight is ready. Just sign it and send it to the airline; "
                             f"their claims email is usually on their website.")}


@app.post("/api/claims/{cid}/billing/setup")
def billing_setup(cid: str):
    """Create the Stripe customer and a card SetupIntent. Card is saved; NO charge is made."""
    owner = require_owner()
    claim = db.get_claim(cid, owner_id=owner)
    if not claim:
        raise HTTPException(404, "Unknown claim")
    if claim.get("stripe_customer_id"):
        raise HTTPException(400, "Billing already set up for this claim")
    payload = claim["payload"]
    cust = billing.setup_customer(payload.get("passenger_name", ""),
                                  payload.get("passenger_email", ""))
    if "error" in cust:
        raise HTTPException(502, f"Stripe customer creation failed: {cust['error']}")
    setup = billing.create_card_setup(
        cust["customer_id"],
        idempotency_key=f"{billing.CONNECTOR}-setup-{owner}-{cid}")
    if "error" in setup:
        raise HTTPException(502, f"Stripe card setup failed: {setup['error']}")
    db.update_claim(cid, owner_id=owner, stripe_customer_id=cust["customer_id"],
                    billing_status="card_pending")
    return {"claim_id": cid,
            "stripe_customer_id": cust["customer_id"],
            "client_secret": setup["client_secret"],
            "fee_disclosure": FEE_DISCLOSURE,
            "user_message": ("Before you save your card, the plain-English deal: "
                             + FEE_DISCLOSURE)}


@app.get("/api/claims/{cid}/billing/status")
def billing_status(cid: str):
    """Pollable card-save + billing state for a claim."""
    owner = require_owner()
    claim = db.get_claim(cid, owner_id=owner)
    if not claim:
        raise HTTPException(404, "Unknown claim")
    status = claim.get("billing_status") or "none"
    card_state = _card_state(claim)
    state_msg = {
        "none": "No card on file yet — set up billing when you're ready.",
        "pending": "Your card is saved and ready. Nothing is charged until you confirm the payout.",
        "ready": "Your card is saved and the fee for this claim is settled.",
        "failed": "The last fee charge failed — your card was not billed. We can retry once you've confirmed.",
    }.get(card_state, "Billing state unknown — ask me to check.")
    return {"claim_id": cid, "billing_status": status,
            "card_state": card_state, "fee_disclosure": FEE_DISCLOSURE,
            "user_message": state_msg}


class PayoutIn(BaseModel):
    amount_eur: float = Field(..., gt=0, description="Airline payout the user confirms they received")


@app.post("/api/claims/{cid}/payout-confirmed")
def payout_confirmed(cid: str, payload: PayoutIn):
    """User confirms the airline paid out: charge the 30% contingency fee off-session.

    The fee basis is the STORED compensation tier from the eligibility
    verdict — never the asserted amount. If the asserted payout differs
    from the stored tier, the request is rejected as implausible.
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
    if float(payload.amount_eur) != float(stored_tier):
        raise HTTPException(
            400, f"That doesn't add up — you confirmed €{payload.amount_eur:.2f}, but this "
                 f"claim's EU261 tier is €{stored_tier}. The fee is 30% of the tier amount. "
                 "Mind double-checking what the airline actually paid?")
    cents = billing.fee_cents(stored_tier)
    res = billing.charge_fee(
        claim["stripe_customer_id"], cents,
        f"EU261 contingency fee (30%) on EUR {stored_tier:.2f} payout, claim {cid}",
        idempotency_key=f"{billing.CONNECTOR}-fee-{owner}-{cid}")
    if "error" in res:
        db.update_claim(cid, owner_id=owner, billing_status="fee_failed")
        raise HTTPException(502, f"Stripe charge failed: {res['error']}")
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
