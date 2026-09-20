"""MCP server for the medical-bill-fighter connector (streamable HTTP, port 8580).

AGENT-NATIVE: every tool result carries a `user_message` field — a warm,
ready-to-speak sentence the agent can say verbatim.

Thin wrapper over the same layer as the REST API. Stripe calls go through
the stripe skill CLI; no raw keys. Every tool is owner-scoped: the first line
is require_owner(), and every db read/write filters by owner_id.
"""
from mcp.server.mcpserver import MCPServer

from src import billing, detector, draft as draft_flow, store
from src.billing import CONNECTOR
from src.identity import require_owner
from src.models import CaseIntake, OutcomeReport, PackType, LEGAL_NOTICE
from app import _say, _payoff_message, _reduction_problem

server = MCPServer("medical-bill-fighter")


def create_mcp_app():
    from src.identity import IdentityMiddleware
    mcp_app = server.streamable_http_app(streamable_http_path="/mcp")
    mcp_app.add_middleware(IdentityMiddleware)
    return mcp_app


def _case_or_raise(case_id: str, owner: str) -> dict:
    """Owner-scoped read: claims ownerless life-event drafts on first touch."""
    case = store.get_case(case_id, owner)
    if case is None:
        unowned = store.get_case_unscoped(case_id)
        if unowned is not None and unowned.get("owner_id") is None:
            store.adopt_case(case_id, owner)
            case = store.get_case(case_id, owner)
    if case is None:
        raise ValueError("case not found")
    return case


@server.tool()
def create_case(patient_name: str, patient_dob: str, provider_name: str,
                total_billed: float, billed_patient_responsibility: float,
                service_date: str = "", statement_date: str = "",
                patient_phone: str = "", patient_email: str = "",
                provider_city: str = "", provider_state: str = "") -> dict:
    """Check a medical bill for errors. Returns findings and a one-line payoff summary."""
    owner = require_owner()
    try:
        intake = CaseIntake(patient_name=patient_name, patient_dob=patient_dob,
                            provider_name=provider_name, total_billed=total_billed,
                            billed_patient_responsibility=billed_patient_responsibility,
                            service_date=service_date, statement_date=statement_date,
                            patient_phone=patient_phone, patient_email=patient_email,
                            provider_city=provider_city, provider_state=provider_state)
    except Exception as e:  # validation error from the model
        return {"error": str(e)}
    findings = detector.detect(intake)
    case_id = store.create_case(intake.model_dump(mode="json"), findings, owner_id=owner)
    return _say(_payoff_message(findings, billed_patient_responsibility),
                case_id=case_id, findings=findings, findings_count=len(findings),
                legal_notice=LEGAL_NOTICE)


@server.tool()
def get_case(case_id: str) -> dict:
    """Get the full case: intake, findings, outcome, fee state."""
    owner = require_owner()
    return _case_or_raise(case_id, owner)


@server.tool()
def pack(case_id: str, type: str) -> dict:
    """Build a letter pack: dispute | itemized | assistance | negotiate.
    Returns the file paths of the generated PDFs."""
    owner = require_owner()
    case = _case_or_raise(case_id, owner)
    try:
        pack_type = PackType(type)
    except ValueError:
        return {"error": f"unknown pack type {type!r}; use dispute, itemized, assistance, or negotiate"}
    from src import letters
    paths = letters.build_pack(case, pack_type)
    return _say(f"Your {type} letter pack is ready — download the PDFs and mail them as instructed.",
                case_id=case_id, type=type, files=paths,
                legal_notice=LEGAL_NOTICE)


@server.tool()
def list_packs() -> dict:
    """List the available letter pack types."""
    owner = require_owner()
    return {"packs": [
        {"type": "dispute",
         "name": "Dispute Letter Pack",
         "when": "Your bill has errors (duplicate charges, wrong codes, bad math)"},
        {"type": "itemized",
         "name": "Itemized Bill Request",
         "when": "You only have a summary bill — ask for the full line-item detail"},
        {"type": "assistance",
         "name": "Financial Assistance Application",
         "when": "You can't afford the bill — apply for the hospital's charity care program"},
        {"type": "negotiate",
         "name": "Settlement Negotiation Letter",
         "when": "The bill is valid but unaffordable — offer a lump-sum or payment plan"},
    ]}


@server.tool()
def report_outcome(case_id: str, reduction_amount: float) -> dict:
    """Report the reduction you actually got (off the bill, the dispute pack worked).
    Returns the exact 25% fee and the next billing steps. No charge happens yet."""
    owner = require_owner()
    try:
        outcome = OutcomeReport(reduction_amount=reduction_amount)
    except Exception as e:
        return {"error": str(e)}
    case = _case_or_raise(case_id, owner)
    # Lock: once the fee is charged, the outcome is the basis of the charge.
    if case.get("fee_status") not in (None, "failed"):
        return {"error": ("The fee for this reduction was already charged — the outcome "
                          "is locked. Contact support if the amount was wrong.")}
    problem = _reduction_problem(outcome.reduction_amount, case)
    if problem:
        return {"error": problem}
    record = {
        "reduction_amount": outcome.reduction_amount,
        "fee_cents": billing.contingency_cents(outcome.reduction_amount),
        "fee_rate": billing.FEE_RATE,
        "fee_disclosure": billing.FEE_DISCLOSURE,
    }
    store.set_outcome(case_id, record, owner)
    fee_usd = round(record["fee_cents"] / 100, 2)
    return _say(
        f"That's a ${outcome.reduction_amount:,.2f} reduction — nice work. My fee is "
        f"25% of that, ${fee_usd:,.2f}, and it's only charged if you confirm. "
        "Want to save a card so I can collect it? It takes 30 seconds.",
        case_id=case_id, **record,
        next="Call setup_billing to save a card, then confirm_reduction_charge to be charged.",
        legal_notice=LEGAL_NOTICE,
    )


@server.tool()
def setup_billing(case_id: str) -> dict:
    """Set up billing for the 25% contingency fee. HONEST FEE DISCLOSURE:
    the response states the exact fee terms BEFORE any card is saved."""
    owner = require_owner()
    case = _case_or_raise(case_id, owner)
    intake = case["intake"]
    customer_id = case.get("stripe_customer_id")
    if not customer_id:
        res = billing.setup_customer(
            intake.get("patient_name", "Patient"),
            intake.get("patient_email") or "",
        )
        if "error" in res:
            return {"error": res["error"]}
        customer_id = res["customer_id"]
        store.set_billing(case_id, owner, stripe_customer_id=customer_id)
    setup = billing.create_card_setup(
        customer_id,
        idempotency_key=f"{CONNECTOR}-setup-{owner}-{case_id}")
    if "error" in setup:
        return {"error": setup["error"]}
    return _say(
        billing.FEE_DISCLOSURE + " Tap to save your card and you're all set.",
        case_id=case_id,
        fee_disclosure=billing.FEE_DISCLOSURE,
        fee_rate=billing.FEE_RATE,
        client_secret=setup["client_secret"],
        setup_intent_id=setup["setup_intent_id"],
        note=("Collect the card against this SetupIntent; it will be stored "
              "for the off-session contingency charge described above."),
    )


@server.tool()
def billing_status(case_id: str) -> dict:
    """Poll billing state: card state + whether the 25% reduction fee is settled."""
    owner = require_owner()
    case = _case_or_raise(case_id, owner)
    customer_id = case.get("stripe_customer_id")
    fee_status = case.get("fee_status")
    if not customer_id:
        status, card_state = "none", "none"
        um = "No card on file yet — say the word and I'll set one up."
    elif fee_status == "failed":
        status, card_state = "fee_failed", "failed"
        um = "The last charge attempt failed — nothing was taken. Want to try again?"
    elif fee_status is not None:
        status, card_state = "fee_charged", "ready"
        um = "The 25% reduction fee has been charged — you're all settled."
    else:
        status, card_state = "card_pending", "pending"
        um = "Your card is on file; nothing is charged until you confirm a reduction."
    return _say(um, case_id=case_id, billing_status=status, card_state=card_state,
                fee_rate=billing.FEE_RATE, fee_disclosure=billing.FEE_DISCLOSURE,
                legal_notice=LEGAL_NOTICE)


@server.tool()
def confirm_reduction_charge(case_id: str) -> dict:
    """Charge 25% of the user-confirmed reduction off-session.

    P0 safety: refuses outright when fee_status is not None/"failed", so an
    already-charged case can never be charged again.
    """
    owner = require_owner()
    case = _case_or_raise(case_id, owner)
    outcome = case.get("outcome")
    if not outcome or outcome.get("reduction_amount", 0) <= 0:
        return {"error": "No confirmed reduction on this case. Report the outcome first."}
    problem = _reduction_problem(outcome["reduction_amount"], case)
    if problem:
        return {"error": problem}
    customer_id = case.get("stripe_customer_id")
    if not customer_id:
        return {"error": "No payment method on file. Run setup_billing first."}
    if case.get("fee_status") not in (None, "failed"):
        return {"error": "The 25% fee for this reduction was already charged — nothing more to do."}
    # Fee re-derived server-side from the STORED outcome — never from the request.
    amount_cents = billing.contingency_cents(outcome["reduction_amount"])
    charge = billing.charge_fee(
        customer_id, amount_cents,
        f"Medical Bill Fighter fee — 25% of ${outcome['reduction_amount']:,.2f} confirmed reduction",
        idempotency_key=f"{CONNECTOR}-fee-{owner}-{case_id}",
    )
    if "error" in charge:
        store.set_billing(case_id, owner, fee_status="failed")
        return {"error": charge["error"]}
    store.set_billing(case_id, owner, fee_cents=amount_cents, fee_status=charge["status"])
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


@server.tool()
def start_bill_check(payload: dict) -> dict:
    """Start the one-question-at-a-time bill check (golden path).
    Returns case_id + next_question; call answer_question with each answer."""
    owner = require_owner()
    state = draft_flow.new_draft(prefill=payload)
    draft_record = {"status": "draft", "draft_state": state,
                    "prefill": payload, "answers": state["answers"]}
    case_id = store.create_case(draft_record, [], owner_id=owner)
    first_q = draft_flow._question_for(state["step"])
    return _say(f"Let's check that bill for errors — takes about a minute. {first_q}",
                case_id=case_id, draft=True, next_question=first_q,
                legal_notice=LEGAL_NOTICE)


@server.tool()
def answer_question(case_id: str, answer: str) -> dict:
    """Answer the current draft question; advances the conversation."""
    owner = require_owner()
    case = _case_or_raise(case_id, owner)
    intake = case["intake"]
    if intake.get("status") != "draft":
        return _say("This case is already complete — ask me to re-check it or open a new one.",
                    case_id=case_id, done=True)
    state = intake["draft_state"]
    result = draft_flow.apply_answer(state, answer)
    if not result["done"]:
        import json
        from src.store import _db
        record = {"status": "draft", "draft_state": state,
                  "prefill": intake.get("prefill", {}), "answers": state["answers"]}
        with _db() as conn:
            conn.execute("UPDATE cases SET intake_json = ? WHERE id = ? AND owner_id = ?",
                         (json.dumps(record), case_id, owner))
        return _say(result["user_message"], case_id=case_id, done=False,
                    next_question=result["question"], legal_notice=LEGAL_NOTICE)
    parsed: CaseIntake = result["case"]
    payload = parsed.model_dump(mode="json")
    findings = detector.detect(parsed)
    import json
    from src.store import _db
    with _db() as conn:
        conn.execute("UPDATE cases SET intake_json = ? WHERE id = ? AND owner_id = ?",
                     (json.dumps(payload), case_id, owner))
    store.set_findings(case_id, findings, owner)
    total = parsed.billed_patient_responsibility
    return _say(_payoff_message(findings, total),
                case_id=case_id, done=True, findings=findings,
                findings_count=len(findings), legal_notice=LEGAL_NOTICE)


if __name__ == "__main__":
    import os as _os

    import uvicorn

    port = int(_os.environ.get("MCP_PORT", "8580"))
    uvicorn.run(create_mcp_app(), host="127.0.0.1", port=port, log_level="warning")
