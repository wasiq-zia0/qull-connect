"""FastAPI REST API for the medical-bill-fighter connector.

AGENT-NATIVE: every response carries a `user_message` field — a warm,
ready-to-speak sentence the agent can say verbatim. Every flow is demoable
inside a plain chat transcript.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Literal
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from pydantic import ConfigDict, BaseModel, Field

from src import billing, detector, draft as draft_flow, letters, store
from src.billing import CONNECTOR
from src.identity import IdentityMiddleware, IdentityError, require_owner
from src.limits import BodySizeLimitMiddleware, RateLimitMiddleware
from src.models import LEGAL_NOTICE, CaseIntake, OutcomeReport, PackType
from src.letters import describe_packs

# Shared suite event bus (~/workspace/connectors/life-events).

_PROD = os.environ.get("ENV") == "production"
_PUBLIC_DOCS = os.environ.get("PUBLIC_DOCS") == "1"
app = FastAPI(
    title="Medical Bill Fighter",
    version="0.2.0",
    description=(
        "Spots errors in medical bills and EOBs, builds dispute letter packs, "
        "and charges 25% of the user-confirmed reduction. " + LEGAL_NOTICE
    ),
    docs_url="/docs" if (_PUBLIC_DOCS or not _PROD) else None,
    redoc_url="/redoc" if (_PUBLIC_DOCS or not _PROD) else None,
    openapi_url="/openapi.json" if (_PUBLIC_DOCS or not _PROD) else None,
)

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


def _say(msg: str, **extra: Any) -> dict[str, Any]:
    return {"user_message": msg, **extra}


def _get_case_or_404(case_id: str, owner: str) -> dict[str, Any]:
    """Owner-scoped read: 404 unless the case belongs to the caller. Legacy ownerless drafts remain inaccessible."""
    case = store.get_case(case_id, owner)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    return case


def _bill_total(case: dict[str, Any]) -> float | None:
    """Stored bill total (the outcome sanity-check reference), or None if unknown."""
    intake = case.get("intake") or {}
    total = intake.get("billed_patient_responsibility")
    if total is None:
        prefill = intake.get("prefill") or {}
        total = prefill.get("billed_patient_responsibility") or prefill.get("total")
    try:
        total = float(total)
    except (TypeError, ValueError):
        return None
    return total if total > 0 else None


def _reduction_problem(reduction: float, case: dict[str, Any]) -> str | None:
    """Sanity check on an asserted reduction: it cannot exceed the stored
    bill total. Returns a plain-language problem, or None if sane."""
    total = _bill_total(case)
    if total is not None and reduction > total:
        return (f"That reduction (${reduction:,.2f}) is greater than the bill total "
                f"(${total:,.2f}) I have on file — a reduction can't be larger than the "
                f"bill. Double-check the amount and try again.")
    return None


def _payoff_message(findings: list[dict[str, Any]], total: float) -> str:
    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]
    if errors:
        first = errors[0]
        return (f"Done — I checked your bill and found {len(errors)} likely error(s). "
                f"Biggest one: {first['explanation']} "
                "Want me to draft the dispute letter? One tap and it's ready to send.")
    if warnings:
        return (f"I went through your ${total:,.0f} bill and flagged {len(warnings)} thing(s) "
                "to discuss with your provider or insurer. These flags do not establish an error. "
                "Would you like a letter template?")
    return (f"Good news — your ${total:,.0f} bill has no flagged issues under these limited checks. This does not verify that the bill is correct. "
            "If anything changes, I'm here. Want me to check anything else?")


# ---------------------------------------------------------------- health
@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "medical-bill-fighter"}


# ---------------------------------------------------------------- life events (receive fan-out)
class SafeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class BillingConsent(SafeModel):
    accept_fee_terms: Literal[True]


class ChargeConsent(SafeModel):
    fee_amount_cents: int = Field(..., gt=0)
    confirm_fee: Literal[True] = Field(..., description="Explicit permission to charge the disclosed fee for this record.")


class LifeEvent(SafeModel):
    event_type: str = Field(min_length=1, max_length=80)
    payload: dict[str, Any] = Field(default_factory=dict)


@app.post("/api/life-events")
def receive_life_event(event: LifeEvent) -> dict:
    """Receive a fanned-out life event. Creates a draft case and returns the nudge."""
    owner = require_owner()
    if event.event_type != "medical_bill_received":
        return _say(
            "That one's not mine — I'll leave it to the right connector.",
            handled=False, event_type=event.event_type,
        )
    payload = event.payload or {}
    total = payload.get("total")
    import math
    try:
        if total is not None and (not math.isfinite(float(total)) or not 0 < float(total) <= 1_000_000):
            raise ValueError
        amount_bit = f" ${float(total):,.0f}" if total else ""
    except (ValueError, TypeError):
        raise HTTPException(422, "total must be a positive finite amount up to 1,000,000")
    state = draft_flow.new_draft(prefill=payload)
    draft_record = {"status": "draft", "draft_state": state,
                    "prefill": dict(state["answers"]), "answers": state["answers"]}
    case_id = store.create_case(draft_record, [], owner_id=owner)
    provider = str(payload.get("provider_name", "your provider"))[:200]
    nudge = (f"I can help review that{amount_bit} bill from {provider} for possible billing errors. "
             "Would you like to enter the bill details?")
    return _say(
        nudge,
        handled=True, case_id=case_id, draft=True,
        next_question=draft_flow._question_for(state["step"]),
        legal_notice=LEGAL_NOTICE,
    )


# ---------------------------------------------------------------- conversational intake (golden path)
class DraftAnswer(SafeModel):
    answer: str = Field(min_length=1, max_length=4000)


@app.post("/api/cases/draft", status_code=201)
def start_draft(payload: dict[str, Any] = {}) -> dict:
    """Start the one-question-at-a-time bill check."""
    owner = require_owner()
    state = draft_flow.new_draft(prefill=payload)
    draft_record = {"status": "draft", "draft_state": state,
                    "prefill": dict(state["answers"]), "answers": state["answers"]}
    case_id = store.create_case(draft_record, [], owner_id=owner)
    first_q = draft_flow._question_for(state["step"])
    return _say(
        f"Let's check that bill for errors — takes about a minute. {first_q}",
        case_id=case_id, draft=True, next_question=first_q,
        legal_notice=LEGAL_NOTICE,
    )


@app.post("/api/cases/{case_id}/answer")
def answer_question(case_id: str, body: DraftAnswer) -> dict:
    """Answer the current draft question; advances the conversation."""
    owner = require_owner()
    case = _get_case_or_404(case_id, owner)
    intake = case["intake"]
    if intake.get("status") != "draft":
        return _say("This case is already complete — ask me to re-check it or open a new one.",
                    case_id=case_id, done=True)
    state = intake["draft_state"]
    result = draft_flow.apply_answer(state, body.answer)
    if not result["done"]:
        # persist progress
        record = {"status": "draft", "draft_state": state,
                  "prefill": intake.get("prefill", {}), "answers": state["answers"]}
        _replace_intake(case_id, record, owner)
        return _say(result["user_message"], case_id=case_id, done=False,
                    next_question=result["question"], legal_notice=LEGAL_NOTICE)
    # finalized: run detection, persist the real case
    parsed: CaseIntake = result["case"]
    payload = parsed.model_dump(mode="json")
    findings = detector.detect(parsed)
    _replace_intake(case_id, payload, owner)
    store.set_findings(case_id, findings, owner)
    total = parsed.billed_patient_responsibility
    return _say(
        _payoff_message(findings, total),
        case_id=case_id, done=True, findings=findings,
        findings_count=len(findings), legal_notice=LEGAL_NOTICE,
    )


def _replace_intake(case_id: str, intake: dict[str, Any], owner: str) -> None:
    # store.py keeps intake immutable; add a small dedicated updater here.
    import sqlite3
    from src.store import _db
    with _db() as conn:
        conn.execute("UPDATE cases SET intake_json = ? WHERE id = ? AND owner_id = ?",
                     (json.dumps(intake), case_id, owner))


# ---------------------------------------------------------------- direct intake (agents with structured data)
@app.post("/api/cases", status_code=201)
def create_case(intake: CaseIntake) -> dict:
    owner = require_owner()
    payload = intake.model_dump(mode="json")
    findings = detector.detect(intake)
    case_id = store.create_case(payload, findings, owner_id=owner)
    return _say(
        _payoff_message(findings, intake.billed_patient_responsibility),
        case_id=case_id, findings=findings, findings_count=len(findings),
        legal_notice=LEGAL_NOTICE,
    )


@app.get("/api/cases/{case_id}")
def get_case(case_id: str) -> dict:
    owner = require_owner()
    case = _get_case_or_404(case_id, owner)
    if case["intake"].get("status") == "draft":
        state = case["intake"]["draft_state"]
        q = draft_flow._question_for(state["step"])
        return _say(f"We're mid-check — next up: {q}", **{**case, "legal_notice": LEGAL_NOTICE,
                                                          "next_question": q})
    findings = case.get("findings") or []
    return _say(_payoff_message(findings, case["intake"].get("billed_patient_responsibility", 0)),
                **{**case, "legal_notice": LEGAL_NOTICE})


# ---------------------------------------------------------------- letter packs
@app.get("/api/cases/{case_id}/pack")
def get_pack(case_id: str,
             type: PackType = Query(..., description="dispute|itemized|assistance|negotiate")) -> Response:
    owner = require_owner()
    case = _get_case_or_404(case_id, owner)
    if case["intake"].get("status") == "draft":
        raise HTTPException(status_code=400, detail="finish the bill check first")
    try:
        pdf = letters.build_pack(case, type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="medical-bill-{type}-{case_id}.pdf"'},
    )


@app.get("/api/packs")
def list_packs() -> dict:
    owner = require_owner()
    return _say(
        "I've got four letter packs: a dispute letter built from the findings, an "
        "itemized-bill request, a financial-assistance request, and a prompt-pay "
        "negotiation script. Say which one and I'll make the PDF.",
        packs=describe_packs(), legal_notice=LEGAL_NOTICE,
    )


# ---------------------------------------------------------------- outcome + billing
@app.post("/api/cases/{case_id}/outcome")
def report_outcome(case_id: str, outcome: OutcomeReport) -> dict:
    owner = require_owner()
    case = _get_case_or_404(case_id, owner)
    if case["intake"].get("status") == "draft":
        raise HTTPException(409, "Complete the bill review before reporting an outcome.")
    # Lock: once the fee is charged, the outcome is the basis of the charge and
    # can no longer be overwritten.
    if case.get("fee_status") not in (None, "failed"):
        raise HTTPException(
            status_code=400,
            detail="The fee for this reduction was already charged — the outcome "
                   "is locked. Contact support if the amount was wrong.",
        )
    problem = _reduction_problem(outcome.reduction_amount, case)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    record = {
        "reduction_amount": outcome.reduction_amount,
        "fee_cents": billing.contingency_cents(outcome.reduction_amount),
        "fee_rate": billing.FEE_RATE,
        "fee_disclosure": billing.FEE_DISCLOSURE,
    }
    if not store.set_outcome(case_id, record, owner):
        raise HTTPException(409, "The fee basis was locked by a payment confirmation. Retry the original payment or contact support.")
    fee_usd = round(record["fee_cents"] / 100, 2)
    if outcome.reduction_amount == 0:
        return _say("No reduction was reported, so no fee is due.", case_id=case_id, **record, legal_notice=LEGAL_NOTICE)
    return _say(
        f"That's a ${outcome.reduction_amount:,.2f} reduction — nice work. My fee is "
        f"25% of that, ${fee_usd:,.2f}, and it's only charged if you confirm. "
        "Want to save a card so I can collect it? It takes 30 seconds.",
        case_id=case_id, **record,
        next=("POST /api/cases/{id}/billing/setup to save a card, then "
              "POST /api/cases/{id}/reduction-confirmed to be charged."),
        legal_notice=LEGAL_NOTICE,
    )


@app.post("/api/cases/{case_id}/billing/setup")
def billing_setup(case_id: str, consent: BillingConsent) -> dict:
    """Create the Stripe customer + SetupIntent.

    HONEST FEE DISCLOSURE (shown BEFORE any card is saved):
    the response states the exact fee terms in plain language.
    """
    owner = require_owner()
    case = _get_case_or_404(case_id, owner)
    intake = case["intake"]
    customer_id = case.get("stripe_customer_id")
    if not customer_id:
        res = billing.setup_customer(
            intake.get("patient_name", "Patient"),
            intake.get("patient_email") or "",
            idempotency_key=f"{CONNECTOR}-customer-{owner}-{case_id}",
        )
        if "error" in res:
            raise HTTPException(status_code=502, detail=res["error"])
        customer_id = res["customer_id"]
        store.set_billing(case_id, owner, stripe_customer_id=customer_id)
    setup = billing.create_card_setup(
        customer_id,
        idempotency_key=f"{CONNECTOR}-setup-{owner}-{case_id}")
    if "error" in setup:
        raise HTTPException(status_code=502, detail=setup["error"])
    store.set_billing(case_id, owner, stripe_setup_intent_id=setup.get("setup_intent_id"), checkout_session_id=setup.get("checkout_session_id"))
    return _say(
        billing.FEE_DISCLOSURE + " Tap to save your card and you're all set.",
        case_id=case_id,
        fee_disclosure=billing.FEE_DISCLOSURE,
        fee_rate=billing.FEE_RATE,
        client_secret=setup.get("client_secret"), setup_url=setup.get("setup_url"), checkout_session_id=setup.get("checkout_session_id"),
        setup_intent_id=setup["setup_intent_id"],
        note=("Open setup_url to save a payment method securely, then check billing/status. A separate explicit confirmation is required for the disclosed fee."),
    )


@app.get("/api/cases/{case_id}/billing/status")
def billing_status(case_id: str) -> dict:
    """Pollable billing state: card state + whether the 25% reduction fee is settled."""
    owner = require_owner()
    case = _get_case_or_404(case_id, owner)
    customer_id = case.get("stripe_customer_id")
    fee_status = case.get("fee_status")
    if not customer_id:
        status, card_state = "none", "none"
        um = "No card on file yet — say the word and I'll set one up."
    elif fee_status == "failed":
        status, card_state = "fee_failed", "failed"
        um = "The last charge attempt failed — nothing was taken. Want to try again?"
    elif fee_status == "succeeded":
        status, card_state = "fee_charged", "ready"
        um = "The 25% reduction fee has been charged — you're all settled."
    else:
        status, card_state = "card_pending", "pending"
        verified = billing.retrieve_card_setup(customer_id, setup_intent_id=case.get("stripe_setup_intent_id"), checkout_session_id=case.get("checkout_session_id"))
        if verified.get("status") == "succeeded":
            status, card_state = "card_ready", "ready"
        um = "Payment method ready; confirm the disclosed fee to continue." if card_state == "ready" else "Complete the secure setup link before confirming a fee."
    return _say(
        um,
        case_id=case_id,
        billing_status=status,
        card_state=card_state,
        fee_rate=billing.FEE_RATE,
        fee_disclosure=billing.FEE_DISCLOSURE,
        legal_notice=LEGAL_NOTICE,
    )


@app.get("/api/cases/{case_id}/fee-quote")
def fee_quote(case_id: str) -> dict:
    """Quote 25% of this case's stored, user-reported reduction without charging."""
    owner = require_owner()
    case = _get_case_or_404(case_id, owner)
    outcome = case.get("outcome")
    if not outcome:
        raise HTTPException(409, "Report the actual bill reduction before requesting a fee quote.")
    problem = _reduction_problem(outcome["reduction_amount"], case)
    if problem:
        raise HTTPException(409, problem)
    cents = billing.contingency_cents(outcome["reduction_amount"])
    return {"case_id": case_id, "reduction_amount": outcome["reduction_amount"],
            "fee_amount_cents": cents, "currency": "usd", "fee_disclosure": billing.FEE_DISCLOSURE,
            "already_paid": case.get("fee_status") == "succeeded", "requires_confirmation": cents > 0,
            "user_message": f"The fee for the recorded ${outcome['reduction_amount']:.2f} reduction is ${cents / 100:.2f} USD. Confirm only if the reduction and fee are correct."}


@app.post("/api/cases/{case_id}/reduction-confirmed")
def reduction_confirmed(case_id: str, consent: ChargeConsent) -> dict:
    """Charge 25% of the user-confirmed reduction off-session."""
    owner = require_owner()
    case = _get_case_or_404(case_id, owner)
    outcome = case.get("outcome")
    if not outcome or outcome.get("reduction_amount", 0) <= 0:
        raise HTTPException(
            status_code=400,
            detail="No confirmed reduction on this case. Report the outcome first "
                   "(POST /api/cases/{id}/outcome).",
        )
    problem = _reduction_problem(outcome["reduction_amount"], case)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    customer_id = case.get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(
            status_code=400,
            detail="No payment method on file. Complete billing setup first "
                   "(POST /api/cases/{id}/billing/setup).",
        )
    if case.get("fee_status") not in (None, "failed", "pending"):
        raise HTTPException(
            status_code=400,
            detail="The 25% fee for this reduction was already charged — nothing more to do.",
        )
    # Fee re-derived server-side from the STORED outcome — never from the request.
    # The stored customer id is used; nothing from the request reaches Stripe.
    amount_cents = billing.contingency_cents(outcome["reduction_amount"])
    if consent.fee_amount_cents != amount_cents:
        raise HTTPException(409, detail={"error": "fee_quote_changed", "fee_amount_cents": amount_cents})
    if not store.reserve_fee(case_id, owner, outcome):
        raise HTTPException(409, "The outcome changed or is already paid. Reload the case and review the fee.")
    charge = billing.charge_fee(
        customer_id, amount_cents,
        f"Medical Bill Fighter fee — 25% of ${outcome['reduction_amount']:,.2f} confirmed reduction",
        idempotency_key=f"{CONNECTOR}-fee-{owner}-{case_id}",
        setup_intent_id=case.get("stripe_setup_intent_id"), checkout_session_id=case.get("checkout_session_id"), consent=consent.confirm_fee,
    )
    if "error" in charge:
        store.set_billing(case_id, owner, fee_status="pending", stripe_payment_intent_id=charge.get("payment_intent_id"))
        return _payment_error(charge)
    store.set_billing(case_id, owner, fee_cents=amount_cents, fee_status=charge["status"], stripe_payment_intent_id=charge["payment_intent_id"])
    return _say(
        f"All set — ${round(amount_cents / 100, 2):,.2f} collected (25% of your "
        f"${outcome['reduction_amount']:,.2f} reduction). You kept the rest. 🎉",
        case_id=case_id,
        reduction_amount=outcome["reduction_amount"],
        fee_cents=amount_cents,
        fee_usd=round(amount_cents / 100, 2),
        payment_intent_id=charge["payment_intent_id"],
        status=charge["status"],
        fee_disclosure=billing.FEE_DISCLOSURE,
    )


@app.delete("/api/data")
def delete_my_data() -> dict:
    """Delete the authenticated user's operational data. Payment/accounting records remain with the processor and protected billing ledger."""
    owner = require_owner()
    with store._db() as conn:
        conn.execute("DELETE FROM cases WHERE owner_id=?", (owner,))
    return {"deleted": True, "retained": "Required payment/accounting records and processor records; backups follow the operator's retention policy.", "user_message": "Your operational records for this connector were deleted. Required payment records are retained separately."}



def _payment_error(result: dict) -> JSONResponse:
    code = result.get("code")
    status = {"payment_processing": 202, "billing_conflict": 409, "payment_not_captured": 409,
              "billing_configuration": 503, "billing_unavailable": 503, "billing_busy": 503,
              "billing_failed": 503, "invalid_fee": 422, "consent_required": 422}.get(code, 402)
    return JSONResponse(status_code=status, content=result)


from api_support import install_api_contract
install_api_contract(app, "medical-bill-fighter", app.title)
