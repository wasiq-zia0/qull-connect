"""401k-match connector: finds uncaptured employer 401(k) match and delivers a fix plan.

Agent-native design: the agent is the UI. EVERY success response carries a
`user_message` field — a warm, ready-to-speak sentence the agent can say
verbatim, so every flow is demoable inside a plain chat transcript.

Flows:
  Golden path (conversational):  trigger -> POST /api/life-events (nudge) ->
      POST /api/plans/draft -> answer salary+contribution -> confirm match
      formula (default 50%-up-to-6%) -> payoff summary.
  Advanced path: POST /api/plans with the full intake in one call.
  Money flow:  POST /api/plans/{id}/billing/setup (save card, no charge) ->
      POST /api/plans/{id}/pay ($99.00 once, off-session) ->
      GET /api/plans/{id}/pack (unlocked).

Life events: receives `job_change` via POST /api/life-events (shared bus
fan-out) and emits `job_change` when the user mentions a new employer, so
sibling connectors (e.g. final-paycheck) can act too.
"""
import os
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import ConfigDict, BaseModel, Field

from src import billing
from src.calc import calculate, ANNUAL_FEE_DOLLARS, PAY_PERIODS
from src.db import save_plan, get_plan, set_paid, set_billing, save_draft, get_draft, delete_draft
from src.billing import setup_customer, create_card_setup, charge_fee, CONNECTOR
from src.converse import parse_money, parse_pct, parse_match, is_affirmative
from src.identity import IdentityMiddleware, IdentityError, require_owner
from src.limits import BodySizeLimitMiddleware, RateLimitMiddleware

_PROD = os.environ.get("ENV") == "production"
_PUBLIC_DOCS = os.environ.get("PUBLIC_DOCS") == "1"
app = FastAPI(title="401k-match API", version="0.1.0",
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

DISCLAIMER = (
    "Educational/financial-education tool only — NOT financial advice. "
    "Confirm your match formula, vesting, and contribution rules with your "
    "plan administrator before changing anything."
)

FEE_DISCLOSURE = (
    "You will be charged a one-time $99.00 for this match analysis and fix plan. "
    "There is no subscription or automatic renewal. Charged only after you confirm."
)

NUDGE_JOB_CHANGE = (
    "New job, new 401(k). Most people leave free match money on the table in "
    "year one. Tell me your salary and contribution % and I'll check yours in 30 seconds."
)

Q_BASICS = (
    "What's your annual salary and your current 401(k) contribution %? "
    "For example: $120k and 4%."
)
Q_MATCH = (
    "An example formula matches 50% of contributions up to 6% of salary. Use your confirmed plan formula, or choose this example: "
    "say yes for the example, or enter a formula like '100% up to 4%'."
)
Q_BASICS_RETRY = (
    "I didn't quite catch that — what's your annual salary and current "
    "contribution %? For example: $120k and 4%."
)
Q_MATCH_RETRY = (
    "Hmm, I didn't get the formula — try 'yes' for the common 50%-up-to-6%, "
    "or tell me yours like '100% up to 4%'."
)


def sanitize_text(value: str, max_length: int = 120) -> str:
    """Treat all user-supplied text as untrusted data.

    Strips control characters and caps length so user input can never alter
    rendered output, logs, or downstream queries/instructions. The name is
    only ever stored as a JSON string and used as a Stripe customer label —
    never interpolated into prompts, SQL, or shell commands.
    """
    cleaned = "".join(ch for ch in value if ch.isprintable()).strip()
    return cleaned[:max_length]


def _money(v: float) -> str:
    return f"${v:,.0f}"


class SafeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class BillingConsent(SafeModel):
    accept_fee_terms: Literal[True]


class ChargeConsent(SafeModel):
    fee_amount_cents: int = Field(..., gt=0)
    confirm_fee: Literal[True] = Field(..., description="Explicit permission to charge the disclosed fee for this record.")


class PlanIn(SafeModel):
    name: str = Field(..., min_length=1, max_length=200, description="Plan holder name")
    salary: float = Field(..., gt=0, le=360000, description="Annual gross salary in dollars")
    pay_frequency: Literal["weekly", "biweekly", "semimonthly", "monthly"]
    current_contrib_pct: float = Field(..., ge=0, le=100,
                                      description="Current per-paycheck contribution as % of salary")
    match_pct: float = Field(..., ge=0, le=200,
                            description="Employer match rate, e.g. 50 = matches 50% of contributions")
    match_cap_pct: float = Field(..., ge=0, le=100,
                                 description="Employer matches up to this % of salary, e.g. 6")
    true_up: bool = Field(default=False, description="Employer runs a year-end true-up")
    new_employer: str | None = Field(default=None, max_length=200,
                                     description="Optional: new employer name — emits a job_change event so sibling connectors can help")


class BillingSetupIn(SafeModel):
    accept_fee_terms: Literal[True]
    email: str = Field(default="", max_length=200)


class DraftAnswerIn(SafeModel):
    answer: str = Field(..., min_length=1, max_length=500)


class LifeEventIn(SafeModel):
    event_type: str
    payload: dict = Field(default_factory=dict)


# ---------- speak helpers ----------

def speak_summary(plan: dict) -> str:
    r = plan["result"]
    if r["uncaptured_match_annual"] > 0:
        return (
            f"Ran your numbers: at {r['current_contrib_pct']}% you're capturing "
            f"{_money(r['employer_match_now'])} of your {_money(r['max_annual_employer_match'])} "
            f"possible match — leaving {_money(r['uncaptured_match_annual'])} a year on the table. "
            f"The modeled minimum match-capture rate is {r['required_contrib_pct']}% (${r['new_per_paycheck_contrib']:,.2f} per paycheck). "
            f"Confirm your plan rules before changing contributions. Want the full step-by-step fix plan? It's a one-time $99, "
            f"charged once after you confirm — just say the word."
        )
    return (
        f"Good news — at {r['current_contrib_pct']}% you're already capturing your full "
        f"{_money(r['max_annual_employer_match'])} employer match. Nothing to fix."
    )


def speak_pack(plan: dict) -> str:
    r = plan["result"]
    msg = (
        f"Your illustration uses a minimum match-capture contribution of {r['required_contrib_pct']}% — "
        f"that's ${r['new_per_paycheck_contrib']:,.2f} per paycheck. In your provider's site or app, "
        f"check the contribution rules and effective date with your plan administrator before making a change."
    )
    if r["true_up"]:
        msg += " Your reported plan includes a true-up; confirm its eligibility, timing, and calculation rules with the administrator."
    else:
        msg += " No true-up here, so the sooner you change it, the more match you capture."
    return msg


# ---------- plan storage ----------

def _store_plan(inputs: dict, owner_id: str | None) -> dict:
    result = calculate(
        salary=inputs["salary"],
        pay_frequency=inputs["pay_frequency"],
        current_contrib_pct=inputs["current_contrib_pct"],
        match_pct=inputs["match_pct"],
        match_cap_pct=inputs["match_cap_pct"],
        true_up=inputs.get("true_up", False),
    )
    plan = {
        "id": uuid.uuid4().hex[:12],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "inputs": inputs,
        "result": result,
        "paid": False,
        "stripe_customer_id": None,
        "stripe_setup_intent_id": None,
        "stripe_payment_intent_id": None,
        "paid_at": None,
        "owner_id": owner_id,
    }
    save_plan(plan)
    return plan


def _summary(plan: dict) -> dict:
    r = plan["result"]
    return {
        "plan_id": plan["id"],
        "paid": plan["paid"],
        "pack_locked": not plan["paid"],
        "uncaptured_match_annual": r["uncaptured_match_annual"],
        "current_employer_match": r["employer_match_now"],
        "max_annual_employer_match": r["max_annual_employer_match"],
        "recommended_contrib_pct": r["required_contrib_pct"],
        "projected_annual_gain": r["projected_annual_gain"],
        "user_message": speak_summary(plan),
        "disclaimer": DISCLAIMER,
    }


def _pack(plan: dict) -> dict:
    r = plan["result"]
    steps = [
        "Log in to your 401(k) provider's site or app (e.g. Fidelity, Vanguard, Empower, Schwab).",
        "Open your plan's contribution settings — look for 'Contributions', 'Manage contributions', or 'Deferral elections'.",
        f"Ask whether a {r['required_contrib_pct']}% contribution captures the modeled match under your plan rules. This is a minimum match illustration, not a recommendation to reduce existing contributions.",
        "Confirm the change and note the effective date — it usually applies to the next paycheck or within 1–2 pay cycles.",
        f"Verify the next pay stub shows ${r['new_per_paycheck_contrib']} going to the 401(k).",
    ]
    notes = []
    if r["true_up"]:
        notes.append(
            "You reported a year-end true-up. Its eligibility and payment rules vary by plan; "
            "confirm those rules with the administrator. This full-year illustration does not calculate a midyear catch-up."
        )
    else:
        notes.append(
            "No true-up: employer match is typically calculated per paycheck, so the "
            "extra match accrues from the paycheck the change takes effect onward — "
            "change it as soon as possible."
        )
    if r["irs_limit_binding"]:
        notes.append(
            f"The IRS elective-deferral limit (${r['irs_elective_deferral_limit']:,.0f} for "
            f"{r['irs_limit_tax_year']}) caps the recommendation at {r['required_contrib_pct']}%."
        )
    return {
        "plan_id": plan["id"],
        "paid": True,
        "paid_at": plan["paid_at"],
        "fix_plan": {
            "recommended_contrib_pct": r["required_contrib_pct"],
            "per_paycheck_contrib_dollars": r["new_per_paycheck_contrib"],
            "annual_employee_contrib_dollars": r["new_annual_employee_contrib"],
            "projected_annual_gain_dollars": r["projected_annual_gain"],
            "steps": steps,
            "notes": notes,
        },
        "math": r,
        "user_message": speak_pack(plan),
        "disclaimer": DISCLAIMER,
    }


# ---------- conversational draft flow ----------

def _new_draft(prefill: dict | None = None, source: str = "chat",
               owner_id: str | None = None) -> dict:
    state = {
        "step": "basics",
        "source": source,
        "inputs": {
            "name": "Plan member",
            "pay_frequency": "biweekly",  # most common; disclosed in the payoff message
            "true_up": False,
            **(prefill or {}),
        },
    }
    draft = {
        "id": uuid.uuid4().hex[:12],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "state": state,
        "owner_id": owner_id,
    }
    save_draft(draft)
    return draft


def _draft_question(draft: dict) -> dict:
    state = draft["state"]
    if state["step"] == "match":
        return {"draft_id": draft["id"], "step": "match", "done": False,
                "user_message": Q_MATCH}
    return {"draft_id": draft["id"], "step": "basics", "done": False,
            "user_message": Q_BASICS}


def _finalize_draft(draft: dict, owner_id: str) -> dict:
    inputs = draft["state"]["inputs"]
    inputs = PlanIn(**inputs).model_dump(exclude={"new_employer"})
    plan = _store_plan(inputs, owner_id)
    delete_draft(draft["id"], owner_id)
    summary = _summary(plan)
    summary["user_message"] += (
        " (I assumed biweekly paychecks — say the word if yours differ.)"
    )
    return summary


def _answer_draft(draft: dict, answer: str, owner_id: str) -> dict:
    state = draft["state"]
    inputs = state["inputs"]

    if state["step"] == "basics":
        salary = parse_money(answer)
        pct = parse_pct(answer)
        if salary is None or pct is None or not 0 < salary <= 360000 or not 0 <= pct <= 100:
            return {"draft_id": draft["id"], "step": "basics", "done": False,
                    "user_message": Q_BASICS_RETRY}
        inputs["salary"] = salary
        inputs["current_contrib_pct"] = pct
        state["step"] = "match"
        save_draft(draft)
        return {"draft_id": draft["id"], "step": "match", "done": False,
                "user_message": Q_MATCH}

    if state["step"] == "match":
        parsed = parse_match(answer)
        if parsed is None:
            return {"draft_id": draft["id"], "step": "match", "done": False,
                    "user_message": Q_MATCH_RETRY}
        inputs["match_pct"], inputs["match_cap_pct"] = parsed
        save_draft(draft)
        try:
            return _finalize_draft(draft, owner_id)
        except ValueError:
            raise HTTPException(422, "The formula is outside supported ranges; use match rate 0–200% and cap 0–100%.")

    return {"draft_id": draft["id"], "step": state["step"], "done": False,
            "user_message": Q_BASICS}


# ---------- endpoints ----------

@app.get("/health")
def health():
    return {"ok": True, "service": "401k-match", "version": "0.1.0",
            "user_message": "401(k) Match Finder is up and running."}


@app.post("/api/life-events")
def receive_life_event(event: LifeEventIn):
    """Receive a fanned-out life event from the shared bus.

    job_change -> start a draft plan and return the proactive nudge.
    """
    owner = require_owner()
    if event.event_type != "job_change":
        return {"received": True, "acted": False,
                "user_message": "Noted — that's not a moment I can help with, "
                                "so I'll stay quiet unless your 401(k) comes up."}
    payload = event.payload or {}
    prefill: dict = {}
    if payload.get("salary"):
        try:
            prefill["salary"] = float(payload["salary"])
        except (TypeError, ValueError):
            pass
    if payload.get("current_contrib_pct") is not None:
        try:
            prefill["current_contrib_pct"] = float(payload["current_contrib_pct"])
        except (TypeError, ValueError):
            pass
    if payload.get("name"):
        prefill["name"] = sanitize_text(str(payload["name"]))
    draft = _new_draft(prefill=prefill, source="life-event:job_change", owner_id=owner)
    # If the bus already gave us salary + contribution, skip straight to the match question.
    if "salary" in prefill and "current_contrib_pct" in prefill:
        draft["state"]["step"] = "match"
        save_draft(draft)
        nudge = ("New job, new 401(k) — and I already have your salary and contribution. "
                 + Q_MATCH)
    else:
        employer = sanitize_text(str(payload.get("new_employer", ""))) if payload.get("new_employer") else ""
        if employer:
            nudge = (f"New job at {employer} — new 401(k) too. Most people leave free match "
                     f"money on the table in year one. Tell me your salary and contribution % "
                     f"and I'll check yours in 30 seconds.")
        else:
            nudge = NUDGE_JOB_CHANGE
    return {"received": True, "acted": True, "event_type": "job_change",
            "draft_id": draft["id"], "user_message": nudge}


@app.post("/api/plans", status_code=201)
def create_plan(payload: PlanIn):
    """Advanced path: full intake in one call. Plan stored; pack locked."""
    owner = require_owner()
    inputs = {**payload.model_dump(), "name": sanitize_text(payload.name)}
    new_employer = inputs.pop("new_employer", None)
    try:
        plan = _store_plan(inputs, owner)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    out = _summary(plan)
    return out


@app.post("/api/plans/draft", status_code=201)
def start_draft():
    """Golden path: start the conversational intake. Returns the first question."""
    owner = require_owner()
    draft = _new_draft(owner_id=owner)
    return _draft_question(draft)


@app.post("/api/plans/draft/{draft_id}/answer")
def answer_draft(draft_id: str, payload: DraftAnswerIn):
    """Answer the current draft question; advances the conversation or finishes the plan."""
    owner = require_owner()
    draft = get_draft(draft_id, owner)
    if not draft:
        raise HTTPException(404, "Draft not found")
    return _answer_draft(draft, payload.answer, owner)


@app.get("/api/plans/{plan_id}")
def get_plan_summary(plan_id: str):
    owner = require_owner()
    plan = get_plan(plan_id, owner)
    if not plan:
        raise HTTPException(404, "Plan not found")
    return _summary(plan)


@app.post("/api/plans/{plan_id}/billing/setup")
def billing_setup(plan_id: str, payload: BillingSetupIn):
    """Create the Stripe customer + SetupIntent so the user can save a card."""
    owner = require_owner()
    plan = get_plan(plan_id, owner)
    if not plan:
        raise HTTPException(404, "Plan not found")
    if plan["paid"]:
        raise HTTPException(409, "Plan already paid")
    cust = ({"customer_id": plan["stripe_customer_id"]} if plan.get("stripe_customer_id") else
            setup_customer(plan["inputs"]["name"], payload.email, idempotency_key=f"{CONNECTOR}-customer-{owner}-{plan_id}"))
    if "error" in cust:
        raise HTTPException(502, f"Stripe customer creation failed: {cust['error']}")
    si = create_card_setup(
        cust["customer_id"],
        idempotency_key=f"{CONNECTOR}-setup-{owner}-{plan_id}")
    if "error" in si:
        raise HTTPException(502, f"Stripe SetupIntent failed: {si['error']}")
    set_billing(plan_id, cust["customer_id"], si.get("setup_intent_id"), owner, si.get("checkout_session_id"))
    return {
        "plan_id": plan_id,
        "stripe_customer_id": cust["customer_id"],
        "setup_intent_id": si["setup_intent_id"],
        "client_secret": si.get("client_secret"),
        "setup_url": si.get("setup_url"), "checkout_session_id": si.get("checkout_session_id"),
        "fee_disclosure": FEE_DISCLOSURE,
        "user_message": (
            "Your card setup is ready — saving a card now costs nothing. "
            + FEE_DISCLOSURE + " Ready to save your card?"
        ),
        "note": "Open setup_url to save a payment method securely, then return and check billing/status. Saving a card does not authorize the $99 fee.",
    }


@app.get("/api/plans/{plan_id}/billing/status")
def billing_status(plan_id: str):
    """Pollable billing state: card state + whether the $99 one-time fee is settled."""
    owner = require_owner()
    plan = get_plan(plan_id, owner)
    if not plan:
        raise HTTPException(404, "Plan not found")
    customer_id = plan["stripe_customer_id"]
    if not customer_id:
        status, card_state = "none", "none"
        um = "No card on file yet — say the word and I'll set one up."
    elif plan["paid"]:
        status, card_state = "paid", "ready"
        um = "The $99.00 one-time fee is paid — your fix plan is unlocked."
    else:
        status, card_state = "card_pending", "pending"
        verified = billing.retrieve_card_setup(customer_id, setup_intent_id=plan.get("stripe_setup_intent_id"), checkout_session_id=plan.get("checkout_session_id"))
        if verified.get("status") == "succeeded":
            status, card_state = "card_ready", "ready"
        um = "Payment method ready; confirm the fee to continue." if card_state == "ready" else "Complete the secure card setup link before confirming payment."
    return {"plan_id": plan_id,
            "billing_status": status,
            "card_state": card_state,
            "amount_dollars": ANNUAL_FEE_DOLLARS,
            "fee_amount_cents": 9900, "currency": "usd",
            "fee_disclosure": FEE_DISCLOSURE,
            "user_message": um}


@app.post("/api/plans/{plan_id}/pay")
def pay(plan_id: str, consent: ChargeConsent):
    """Charge the flat $99 one-time fee off-session against the saved card."""
    owner = require_owner()
    plan = get_plan(plan_id, owner)
    if not plan:
        raise HTTPException(404, "Plan not found")
    if plan["paid"]:
        raise HTTPException(409, "Plan already paid")
    if not plan.get("stripe_customer_id"):
        raise HTTPException(400, "Run POST /api/plans/{id}/billing/setup first to save a card")
    if consent.fee_amount_cents != 9900:
        raise HTTPException(409, detail={"error": "fee_quote_changed", "fee_amount_cents": 9900})
    # The fee amount is the server-side ANNUAL_FEE_CENTS constant — never client input.
    # The stored customer id is used; nothing from the request reaches Stripe.
    res = charge_fee(
        plan["stripe_customer_id"],
        idempotency_key=f"{CONNECTOR}-fee-{owner}-{plan_id}",
        setup_intent_id=plan.get("stripe_setup_intent_id"), checkout_session_id=plan.get("checkout_session_id"), consent=consent.confirm_fee)
    if "error" in res:
        return _payment_error(res)
    paid_at = datetime.now(timezone.utc).isoformat()
    # Atomic compare-and-set: a concurrent retry can never double-mark paid.
    if not set_paid(plan_id, plan["stripe_customer_id"], res["payment_intent_id"], paid_at, owner):
        raise HTTPException(409, "Plan already paid")
    return {
        "plan_id": plan_id,
        "paid": True,
        "amount_dollars": ANNUAL_FEE_DOLLARS,
        "fee_disclosure": FEE_DISCLOSURE,
        "payment_intent_id": res["payment_intent_id"],
        "status": res["status"],
        "paid_at": paid_at,
        "user_message": "All set — $99.00 for this analysis is paid, and your full fix plan is unlocked. Here it comes.",
    }


@app.get("/api/plans/{plan_id}/pack")
def get_pack(plan_id: str):
    """Full fix plan. Locked (402) until the $99 one-time fee is paid."""
    owner = require_owner()
    plan = get_plan(plan_id, owner)
    if not plan:
        raise HTTPException(404, "Plan not found")
    if not plan["paid"]:
        raise HTTPException(
            402,
            "Payment required: save a card and pay the $99 one-time fee "
            f"(POST /api/plans/{plan_id}/billing/setup then POST /api/plans/{plan_id}/pay) "
            "to unlock the fix plan.",
        )
    return _pack(plan)


@app.delete("/api/data")
def delete_my_data() -> dict:
    """Delete the authenticated user's operational data. Payment/accounting records remain with the processor and protected billing ledger."""
    owner = require_owner()
    from src.db import _conn
    with _conn() as conn:
        conn.execute("DELETE FROM drafts WHERE owner_id=?", (owner,))
        conn.execute("DELETE FROM plans WHERE owner_id=?", (owner,))
    return {"deleted": True, "retained": "Required payment/accounting records and processor records; backups follow the operator's retention policy.", "user_message": "Your operational records for this connector were deleted. Required payment records are retained separately."}



def _payment_error(result: dict) -> JSONResponse:
    code = result.get("code")
    status = {"payment_processing": 202, "billing_conflict": 409, "payment_not_captured": 409,
              "billing_configuration": 503, "billing_unavailable": 503, "billing_busy": 503,
              "billing_failed": 503, "invalid_fee": 422, "consent_required": 422}.get(code, 402)
    return JSONResponse(status_code=status, content=result)


from api_support import install_api_contract
install_api_contract(app, "401k-match", app.title)
