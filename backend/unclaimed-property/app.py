"""Unclaimed-property connector: REST API.

Agent-native design: the agent is the UI. Every response carries a `user_message`
field — a warm, ready-to-speak sentence the agent can say verbatim — alongside
the machine JSON. Every flow is demoable in a plain chat transcript.

Golden path:  trigger (move life-event) -> one tap -> done.
  1. POST /api/life-events {event_type: "move", payload: {...}}  -> draft search + nudge
  2. PATCH /api/searches/{id} {full_legal_name}                  -> one tap, search is live
  3. GET claim packs                                            -> user files; connector never does

Security: all user-supplied text is treated as untrusted data. It is length-
capped, stripped of control characters, and HTML-escaped before being rendered
into claim packs, nudges, or tool outputs. It can never alter queries or
instructions. No SSN is ever collected or stored.
"""
import html
import json
import os
import re
import sys
from datetime import date
from typing import Literal
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import ConfigDict, BaseModel, EmailStr, Field, field_validator

import store
store.init()
import billing
from identity import IdentityMiddleware, IdentityError, require_owner
from limits import BodySizeLimitMiddleware, RateLimitMiddleware

# Shared life-event bus (suite compounding): one "move" fans out to
# deposit-recovery + moving-concierge + this connector.

BASE_DIR = Path(__file__).parent
STATE_DATA = json.loads((BASE_DIR / "data" / "state_claims.json").read_text())["states"]
STATE_MAP = {s["abbr"]: s for s in STATE_DATA}
STATE_NAMES = {
    "AK": "Alaska", "AL": "Alabama", "AR": "Arkansas", "AZ": "Arizona", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DC": "the District of Columbia", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "IA": "Iowa", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "MA": "Massachusetts", "MD": "Maryland", "ME": "Maine", "MI": "Michigan", "MN": "Minnesota",
    "MO": "Missouri", "MS": "Mississippi", "MT": "Montana", "NC": "North Carolina",
    "ND": "North Dakota", "NE": "Nebraska", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NV": "Nevada", "NY": "New York", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VA": "Virginia",
    "VT": "Vermont", "WA": "Washington", "WI": "Wisconsin", "WV": "West Virginia",
    "WY": "Wyoming",
}
DIRECTORY_URL = "https://unclaimed.org/"
DIRECTORY_SOURCE_URL = "https://www.usa.gov/unclaimed-money"
FEE_RATE = billing.FEE_RATE  # 0.10 (statutory baseline; use billing.fee_rate_for per state)
FEE_DISCLOSURE = (
    "Official state searches and claims are free. This connector currently provides free portal guides; "
    "paid recovery assistance is unavailable pending state-specific fee and agreement review."
)

_PROD = os.environ.get("ENV") == "production"
_PUBLIC_DOCS = os.environ.get("PUBLIC_DOCS") == "1"
app = FastAPI(title="Unclaimed Property Connector", version="0.2.0",
              docs_url="/docs" if (_PUBLIC_DOCS or not _PROD) else None,
              redoc_url="/redoc" if (_PUBLIC_DOCS or not _PROD) else None,
              openapi_url="/openapi.json" if (_PUBLIC_DOCS or not _PROD) else None)

# Middleware order: added last runs first. IdentityMiddleware must run before
# everything else so unauthenticated requests never reach handlers.
app.add_middleware(IdentityMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(BodySizeLimitMiddleware)


@app.exception_handler(IdentityError)
async def identity_error_handler(_, exc: IdentityError):
    msg = str(exc)
    return JSONResponse(status_code=401,
                        content={"detail": msg, "user_message": msg})


# ---------- sanitization ----------

def scrub(text: str, limit: int = 120) -> str:
    """Storage-safe: cap length and strip control characters. Not escaped."""
    return re.sub(r"[\x00-\x1f\x7f]", "", (text or "").strip())[:limit]


def h(text: str, limit: int = 120) -> str:
    """Render-safe: scrub then HTML-escape. Use for every user-supplied value
    embedded in claim packs, nudges, or tool outputs."""
    return html.escape(scrub(text, limit))


# Back-compat alias (single escape point is h(); validators use scrub()).
def clean(text: str, limit: int = 120) -> str:
    return scrub(text, limit)


# ---------- models ----------

class SafeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class StateOfResidence(SafeModel):
    abbr: str = Field(..., min_length=2, max_length=2,
                      description="Two-letter state abbreviation")
    years: str = Field(default="", max_length=40, description="e.g. '2015-2019'")

    @field_validator("abbr")
    @classmethod
    def abbr_upper(cls, v: str) -> str:
        v = v.upper()
        if v not in STATE_MAP:
            raise ValueError(f"unknown state abbreviation: {v}")
        return v

    @field_validator("years")
    @classmethod
    def years_clean(cls, v: str) -> str:
        return clean(v, 40)


class RecentMove(SafeModel):
    from_state: str = Field(..., min_length=2, max_length=2)
    to_state: str = Field(..., min_length=2, max_length=2)

    @field_validator("from_state", "to_state")
    @classmethod
    def state_ok(cls, v: str) -> str:
        v = v.upper()
        if v not in STATE_MAP:
            raise ValueError(f"unknown state abbreviation: {v}")
        return v


class IntakeRequest(SafeModel):
    full_legal_name: str = Field(..., min_length=2, max_length=120,
                                 description="Full legal name to search for")
    prior_names: list[str] = Field(default_factory=list, max_length=10,
                                   description="Maiden/prior names")
    email: EmailStr
    states_of_residence: list[StateOfResidence] = Field(
        ..., min_length=1, max_length=51, description="States where the claimant has lived")
    dob: str | None = Field(default=None,
                            description="YYYY-MM-DD; optional, some states need it at filing")
    dob_consent: bool = Field(
        default=False,
        description="Must be true to store dob. Explicit consent required; never store SSN.")
    recent_move: RecentMove | None = Field(
        default=None,
        description="Optional: emit a 'move' life event so the suite (deposit-recovery, "
                    "moving-concierge) can act on it too.")

    @field_validator("full_legal_name")
    @classmethod
    def name_clean(cls, v: str) -> str:
        return clean(v, 120)

    @field_validator("prior_names")
    @classmethod
    def prior_clean(cls, v: list[str]) -> list[str]:
        return [clean(n, 120) for n in v if n and clean(n, 120)]

    @field_validator("dob")
    @classmethod
    def dob_format(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            parsed = date.fromisoformat(v)
        except ValueError:
            raise ValueError("dob must be YYYY-MM-DD")
        if parsed > date.today():
            raise ValueError("dob cannot be in the future")
        return v

    @field_validator("states_of_residence")
    @classmethod
    def dedupe_states(cls, v: list[StateOfResidence]) -> list[StateOfResidence]:
        seen, out = set(), []
        for s in v:
            if s.abbr not in seen:
                seen.add(s.abbr)
                out.append(s)
        return out


class DraftUpdate(SafeModel):
    full_legal_name: str | None = Field(default=None, min_length=2, max_length=120)
    prior_names: list[str] | None = Field(default=None, max_length=10)
    email: EmailStr | None = None
    dob: str | None = None
    dob_consent: bool | None = None
    states_of_residence: list[StateOfResidence] | None = Field(default=None, min_length=1, max_length=51)

    @field_validator("full_legal_name")
    @classmethod
    def name_clean(cls, v: str | None) -> str | None:
        return clean(v, 120) if v else v

    @field_validator("prior_names")
    @classmethod
    def prior_clean(cls, v: list[str] | None) -> list[str] | None:
        return [clean(n, 120) for n in v if n and clean(n, 120)] if v is not None else None

    @field_validator("dob")
    @classmethod
    def dob_format(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            parsed = date.fromisoformat(v)
        except ValueError:
            raise ValueError("dob must be YYYY-MM-DD")
        if parsed > date.today():
            raise ValueError("dob cannot be in the future")
        return v


class StatusUpdate(SafeModel):
    status: str = Field(..., description="One of: not_started, in_progress, filed, paid, denied")


class BillingConsent(SafeModel):
    accept_fee_terms: Literal[True]


class RecoveryConfirmed(SafeModel):
    fee_amount_cents: int = Field(..., gt=0)
    confirm_fee: Literal[True]
    amount: float = Field(..., gt=0, le=100_000_000, description="Recovered amount in USD")
    state_abbr: str = Field(..., min_length=2, max_length=2, description="State the recovery came from")


class LifeEvent(SafeModel):
    event_type: str = Field(..., min_length=1, max_length=60)
    payload: dict = Field(default_factory=dict)


# ---------- helpers ----------

def _error(status: int, code: str, message: str, user_message: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"error": code, "message": message, "user_message": user_message})


def _get_search_or_404(search_id: str, owner: str) -> dict:
    rec = store.get_search(search_id, owner)
    if rec is None:
        raise HTTPException(status_code=404, detail="search not found")
    return rec


def _checklist(search_id: str, states: list[dict]) -> list[dict]:
    return [{
        "abbr": s["state_abbr"], "name": STATE_MAP[s["state_abbr"]]["name"],
        "portal_url": DIRECTORY_URL,
        "reference_needs_review": True,
        "status": s["status"],
        "claim_pack": f"/api/searches/{search_id}/claim-pack?state={s['state_abbr']}",
    } for s in states]


def _state_list_sentence(abbrs: list[str]) -> str:
    names = [STATE_NAMES[a] for a in abbrs]
    if len(names) == 1:
        return names[0]
    return " and ".join([", ".join(names[:-1]), names[-1]])


def _claim_pack(search: dict, abbr: str) -> dict:
    st = STATE_MAP[abbr]
    name = h(search["full_legal_name"], 120)
    names = [name] + [h(n, 120) for n in search["prior_names"] if n]
    residency = next((r for r in search["states_of_residence"] if r["abbr"] == abbr), None)
    years = residency.get("years", "") if residency else ""
    pack = {
        "search_id": search["id"],
        "state": {"abbr": abbr, "name": st["name"]},
        "portal_url": DIRECTORY_URL,
        "reference_portal_url": st["portal_url"],
        "directory_source_url": DIRECTORY_SOURCE_URL,
        "reference_needs_review": True,
        "filing": st["filing"],
        "steps": [
            f"Open the NAUPA directory and select {st['name']} to reach its current official unclaimed-property program.",
            "Search the state's database for each of these names (try last name only, "
            "and each prior name): " + "; ".join(names),
            "When you find property that is yours, start a claim from that property record.",
            "Fill in the claimant details from the cover sheet below and upload or mail "
            "every document on the checklist.",
            f"Submit, save the confirmation / claim number, then tell me and I'll mark "
            f"{st['name']} as filed.",
            "State searches and claims are free through the official portals. Paid recovery assistance from this connector is currently unavailable.",
        ],
        "document_checklist": st["docs_required"],
        "document_checklist_note": "Examples only. Confirm the exact documents and submission method on the current state portal; do not send sensitive identity documents to this connector.",
        "cover_sheet": {
            "full_legal_name": name,
            "prior_names": [h(n, 120) for n in search["prior_names"] if n],
            "email": h(search["email"], 160),
            "date_of_birth": "(on file)" if search["dob_consent"] and search.get("dob") else "(not provided)",
            "state_of_residence_years": h(years, 40) if years else "(not provided)",
            "note": "This connector prepares claim packs only. It never files claims and never "
                    "sends your information anywhere.",
        },
        "claim_summary": "Use the current state program website to review requirements, search, submit your own claim, and check status. The connector does not perform these actions.",
        "last_verified": None,
        "reference_review_required": True,
    }
    return pack


# ---------- routes ----------

@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "unclaimed-property", "version": "0.2.0",
            "states": len(STATE_MAP),
            "user_message": "Unclaimed-property lookup is up, covering all 50 states plus DC."}


@app.get("/api/states")
def list_states() -> dict:
    owner = require_owner()
    return {"states": [
        {"abbr": s["abbr"], "name": s["name"], "portal_url": DIRECTORY_URL, "reference_portal_url": s["portal_url"], "reference_needs_review": True,
         "filing": s["filing"], "last_verified": None, "note": "Confirm current filing requirements on the official portal."}
        for s in STATE_DATA
    ],
            "count": len(STATE_DATA),
            "user_message": "I can provide official unclaimed-property portal links for all 50 states plus "
                           "DC. Tell me which states you've lived in and I'll build your claim packs."}


@app.post("/api/life-events", status_code=201)
def receive_life_event(evt: LifeEvent) -> JSONResponse:
    """Receive a fanned-out life event from the shared bus.

    Golden path entry point: a 'move' event arrives -> draft search is created
    with from/to states pre-filled -> the response's user_message is the
    proactive nudge the agent speaks to the user.
    """
    owner = require_owner()
    et = clean(evt.event_type, 60).lower()
    payload = evt.payload or {}
    if et == "move":
        store.init()
        from_s = clean(str(payload.get("from_state", "")), 2).upper()
        to_s = clean(str(payload.get("to_state", "")), 2).upper()
        states = [s for s in (from_s, to_s) if s in STATE_MAP]
        if not states:
            return _error(422, "no_states",
                          "move payload needs from_state and/or to_state (2-letter abbr)",
                          "I heard you moved, but I'm not sure which states — which states have "
                          "you lived in?")
        name = clean(str(payload.get("full_legal_name", "")), 120)
        email = clean(str(payload.get("email", "")), 160)
        search_id = store.create_search(
            full_legal_name=name or "(pending)",
            prior_names=[], email=email,
            dob=None, dob_consent=False,
            states_of_residence=[{"abbr": a, "years": ""} for a in states],
            draft=True, owner_id=owner)
        sentence = _state_list_sentence(states)
        if name and name != "(pending)":
            nudge = (f"You just moved, and states often hold unclaimed money under old "
                     f"addresses. I can prepare links and a checklist so you can search {sentence} "
                     f"under your name, {h(name)}. Would you like a guide?")
        else:
            nudge = (f"You just moved, and states often hold unclaimed money under old "
                     f"addresses. I can prepare a guide to help you search {sentence}. Reply with "
                     f"the name you want to use in the guide.")
        return JSONResponse(status_code=201, content={
            "search_id": search_id, "draft": True,
            "states": [STATE_MAP[a]["name"] for a in states],
            "fanned_out_note": "This event is stored only for the authenticated user in this connector.",
            "user_message": nudge,
        })
    return _error(422, "unsupported_event",
                  f"this connector handles 'move' events; got '{et}'",
                  "Got it — that event isn't one I can act on, so I'll sit this one out.")


@app.post("/api/searches", status_code=201)
def create_search(req: IntakeRequest) -> JSONResponse:
    owner = require_owner()
    if req.dob and not req.dob_consent:
        return _error(422, "consent_required",
                      "dob provided without dob_consent=true",
                      "To store your date of birth I need your explicit OK — some states ask "
                      "for it when you file. Either confirm consent or leave the date off.")
    store.init()
    search_id = store.create_search(
        full_legal_name=req.full_legal_name,
        prior_names=req.prior_names,
        email=str(req.email),
        dob=req.dob if req.dob_consent else None,
        dob_consent=req.dob_consent,
        states_of_residence=[s.model_dump() for s in req.states_of_residence],
        draft=False,
        owner_id=owner)
    rec = store.get_search(search_id, owner)
    abbrs = [s["state_abbr"] for s in rec["state_statuses"]]
    fanout_note = None
    if req.recent_move:
        fanout_note = "Move noted for this search only; no data was shared with other connectors."
    return JSONResponse(status_code=201, content={
        "search_id": search_id,
        "claimant": req.full_legal_name,
        "states": _checklist(search_id, rec["state_statuses"]),
        "fanout": fanout_note,
        "user_message": (f"Done — I've prepared claim packs for {_state_list_sentence(abbrs)}. "
                         f"Each pack has the portal link, step-by-step filing instructions, "
                         f"and your cover sheet. Want to walk through the first one?"),
    })


@app.patch("/api/searches/{search_id}")
def update_search(search_id: str, body: DraftUpdate) -> JSONResponse:
    """Conversational step: complete a draft search (the 'one tap' after the nudge)
    or update an existing one. Each call returns a speakable user_message."""
    owner = require_owner()
    rec = _get_search_or_404(search_id, owner)
    if body.dob and not body.dob_consent:
        return _error(422, "consent_required",
                      "dob provided without dob_consent=true",
                      "To store your date of birth I need your explicit OK — say the word or "
                      "leave the date off.")
    if body.full_legal_name:
        rec["full_legal_name"] = body.full_legal_name
    if body.prior_names is not None:
        rec["prior_names"] = body.prior_names
    if body.email is not None:
        rec["email"] = clean(str(body.email), 160)
    if body.dob_consent is False:
        rec["dob_consent"] = False
        rec["dob"] = None
    if body.dob_consent:
        rec["dob_consent"] = True
        if body.dob:
            rec["dob"] = body.dob
    if body.states_of_residence is not None:
        rec["states_of_residence"] = [s.model_dump() for s in body.states_of_residence]
    store.save_search_updates(search_id, rec, owner)
    rec = store.get_search(search_id, owner)
    abbrs = [s["state_abbr"] for s in rec["state_statuses"]]
    if rec["draft"] and rec["full_legal_name"] not in ("", "(pending)"):
        store.set_draft(search_id, False, owner)
        rec = store.get_search(search_id, owner)
        um = (f"Your claim guides for {_state_list_sentence(abbrs)} are ready. "
              f"Use the official portals to search your name and file any claims yourself.")
    else:
        um = "Updated — your search is current."
    return JSONResponse(status_code=200, content={
        "search_id": search_id, "draft": rec["draft"],
        "states": _checklist(search_id, rec["state_statuses"]),
        "user_message": um,
    })


@app.get("/api/searches/{search_id}")
def get_search(search_id: str) -> dict:
    owner = require_owner()
    rec = _get_search_or_404(search_id, owner)
    counts = {}
    for s in rec["state_statuses"]:
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    n = len(rec["state_statuses"])
    paid = counts.get("paid", 0)
    if paid:
        um = f"{paid} of {n} states have paid out so far. The rest are still in play."
    elif counts.get("filed"):
        um = (f"Claims are filed in {counts['filed']} of {n} states — now we wait on the state. "
              f"Update this checklist when you hear from the state; no status monitoring is performed.")
    else:
        um = (f"Your search covers {n} states and nothing is filed yet. "
              f"Say the word and I'll walk you through the first claim pack.")
    return {"search_id": search_id, "draft": rec["draft"],
            "claimant": h(rec["full_legal_name"], 120),
            "states": _checklist(search_id, rec["state_statuses"]),
            "status_counts": counts,
            "recoveries": rec["recoveries"],
            "user_message": um}


@app.get("/api/searches/{search_id}/claim-pack")
def claim_pack(search_id: str, state: str = Query(..., min_length=2, max_length=2)) -> dict:
    owner = require_owner()
    rec = _get_search_or_404(search_id, owner)
    abbr = state.upper()
    if abbr not in STATE_MAP:
        raise HTTPException(status_code=422, detail=f"unknown state abbreviation: {abbr}")
    if not any(s["state_abbr"] == abbr for s in rec["state_statuses"]):
        raise HTTPException(status_code=404,
                            detail=f"state {abbr} is not part of search {search_id}")
    if rec["draft"] or rec["full_legal_name"] in ("", "(pending)"):
        raise HTTPException(status_code=409,
                            detail="I still need your full legal name before the claim pack "
                                   "is ready — reply with it and I'll finish the pack.")
    pack = _claim_pack(rec, abbr)
    pack["user_message"] = (
        f"Here is your {STATE_MAP[abbr]['name']} guide: a link to the current state directory, general filing steps, "
        f"example documents, and your cover sheet. Verify current requirements on the official state site and file the claim yourself.")
    return pack


@app.patch("/api/searches/{search_id}/states/{abbr}")
def update_state_status(search_id: str, abbr: str, body: StatusUpdate) -> dict:
    owner = require_owner()
    _get_search_or_404(search_id, owner)
    abbr = abbr.upper()
    if body.status not in store.STATUSES:
        raise HTTPException(status_code=422,
                            detail=f"status must be one of {store.STATUSES}")
    try:
        res = store.set_state_status(search_id, abbr, body.status, owner)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if res is None:
        raise HTTPException(status_code=404,
                            detail=f"state {abbr} is not part of search {search_id}")
    name = STATE_NAMES.get(abbr, abbr)
    um = {
        "not_started": f"{name} is back on the to-do list.",
        "in_progress": f"Got it — your {name} claim is in progress.",
        "filed": f"Recorded your report that the {name} claim is filed. Check the state portal for its current status.",
        "paid": f"Recorded your report that the {name} claim paid out. No fee is collected by this connector at this time.",
        "denied": f"Sorry to hear {name} was denied. Want help figuring out the next step?",
    }[body.status]
    return {**res, "user_message": um}


@app.post("/api/searches/{search_id}/billing/setup", status_code=409)
def billing_setup(search_id: str, consent: BillingConsent) -> JSONResponse:
    owner = require_owner()
    rec = _get_search_or_404(search_id, owner)
    _paid_service_available(rec)
    res = ({"customer_id": rec["stripe_customer_id"]} if rec.get("stripe_customer_id") else
           billing.setup_customer(rec["full_legal_name"], rec["email"], idempotency_key=f"{billing.CONNECTOR}-customer-{owner}-{search_id}"))
    if "error" in res:
        return _error(502, "stripe_error", res["error"],
                      "I couldn't set up billing just now — let's try again in a bit.")
    customer_id = res["customer_id"]
    setup = billing.create_card_setup(
        customer_id,
        idempotency_key=f"{billing.CONNECTOR}-setup-{owner}-{search_id}")
    if "error" in setup:
        return _error(502, "stripe_error", setup["error"],
                      "I couldn't set up billing just now — let's try again in a bit.")
    store.set_stripe_customer(search_id, customer_id, owner, setup.get("setup_intent_id"), setup.get("checkout_session_id"))
    return JSONResponse(status_code=200, content={
        "search_id": search_id,
        "customer_id": customer_id,
        "client_secret": setup.get("client_secret"),
        "setup_url": setup.get("setup_url"), "checkout_session_id": setup.get("checkout_session_id"),
        "setup_intent_id": setup.get("setup_intent_id"),
        "fee_disclosure": FEE_DISCLOSURE,
        "user_message": ("One last thing before you save your card: " + FEE_DISCLOSURE +
                         " Save your card now and I'll only ever charge it after you've "
                         "confirmed money in hand."),
    })


@app.get("/api/searches/{search_id}/billing/status")
def billing_status(search_id: str) -> dict:
    """Pollable billing state: card state + whether the recovery fee is done."""
    owner = require_owner()
    rec = _get_search_or_404(search_id, owner)
    return {"search_id": search_id, "billing_status": "disabled", "card_state": "unavailable", "fee_disclosure": FEE_DISCLOSURE,
            "user_message": FEE_DISCLOSURE}


@app.post("/api/searches/{search_id}/recovery-confirmed", status_code=409)
def recovery_confirmed(search_id: str, body: RecoveryConfirmed) -> JSONResponse:
    owner = require_owner()
    rec = _get_search_or_404(search_id, owner)
    _paid_service_available(rec)
    abbr = None
    if body.state_abbr:
        abbr = body.state_abbr.upper()
        if abbr not in STATE_MAP:
            raise HTTPException(status_code=422, detail=f"unknown state abbreviation: {abbr}")
    if abbr not in {s["state_abbr"] for s in rec["state_statuses"]}:
        raise HTTPException(422, "Recovery state must be part of this search.")
    # Fee is re-derived server-side from the asserted amount and the recovery
    # state's statutory cap — never from client input. Effective rate ≤ 10%.
    rate = billing.fee_rate_for(abbr)
    fee_cents = billing.contingency_cents(body.amount, abbr)
    if body.fee_amount_cents != fee_cents:
        raise HTTPException(409, detail={"error": "fee_quote_changed", "fee_amount_cents": fee_cents})
    customer_id = store.get_stripe_customer(search_id, owner)
    if not customer_id:
        return _error(409, "billing_not_set_up",
                      "no saved card on this search",
                      "I don't have a card on file yet, so I can't run the fee. Save a card "
                      "first, then confirm the recovery again.")
    if store.has_charged_recovery(search_id, abbr, owner):
        state_bit = f" in {STATE_NAMES[abbr]}" if abbr else ""
        return _error(409, "already_billed",
                      f"a recovery fee was already charged for this search{state_bit}",
                      f"The fee for this recovery{state_bit} was already charged — nothing "
                      "more to do. A recovery in a different state is still chargeable.")
    desc = (f"Unclaimed-property connector fee ({rate:.0%}) for search {search_id}"
            + (f", {abbr}" if abbr else ""))
    charge = billing.charge_fee(
        customer_id, fee_cents, desc,
        idempotency_key=f"{billing.CONNECTOR}-fee-{owner}-{search_id}-{abbr}",
        setup_intent_id=rec.get("stripe_setup_intent_id"), checkout_session_id=rec.get("checkout_session_id"), consent=body.confirm_fee)
    if "error" in charge:
        return JSONResponse(status_code=402, content=charge)
    saved = store.record_recovery(search_id, body.amount, fee_cents, rate, abbr,
                                  charge.get("payment_intent_id"), owner)
    dollars = fee_cents / 100
    return JSONResponse(status_code=200, content={
        "recovery": saved,
        "fee_rate": rate,
        "fee_charged_cents": fee_cents,
        "payment_intent_id": charge.get("payment_intent_id"),
        "status": charge.get("status"),
        "user_message": (f"Confirmed — ${body.amount:,.2f} recovered"
                         + (f" from {STATE_NAMES[abbr]}" if abbr else "")
                         + f". The {rate:.0%} fee is ${dollars:,.2f}. Enjoy the rest!"),
    })


def _paid_service_available(rec: dict) -> None:
    raise HTTPException(409, "Paid recovery assistance is not available. State-specific agreements and fee eligibility are pending review. Official portal links and claim guides remain free.")


@app.delete("/api/data")
def delete_my_data() -> dict:
    """Delete the authenticated user's operational data. Payment/accounting records remain with the processor and protected billing ledger."""
    owner = require_owner()
    store.init()
    with store._conn() as conn:
        conn.execute("DELETE FROM search_states WHERE owner_id=?", (owner,))
        conn.execute("DELETE FROM recoveries WHERE owner_id=?", (owner,))
        conn.execute("DELETE FROM searches WHERE owner_id=?", (owner,))
    return {"deleted": True, "retained": "Required payment/accounting records and processor records; backups follow the operator's retention policy.", "user_message": "Your operational records for this connector were deleted. Required payment records are retained separately."}


from api_support import install_api_contract
install_api_contract(app, "unclaimed-property", app.title)
