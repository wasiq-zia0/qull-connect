"""MCP server for the 401k-match connector (streamable HTTP, port 8579).

Thin wrapper over the same calculation/persistence layer as the REST API.
Stripe calls go through the stripe skill CLI; no raw keys.
"""
from mcp.server.mcpserver import MCPServer

from src.calc import calculate, PAY_PERIODS, ANNUAL_FEE_DOLLARS
from src.db import save_plan, get_plan, set_paid, set_billing, get_draft
from src.billing import setup_customer, create_card_setup, charge_fee, CONNECTOR
from src.identity import require_owner
from app import (_summary, _pack, DISCLAIMER, FEE_DISCLOSURE, sanitize_text,
                 _new_draft, _draft_question, _answer_draft)

server = MCPServer("401k-match")


def create_mcp_app():
    from src.identity import IdentityMiddleware
    mcp_app = server.streamable_http_app(streamable_http_path="/mcp")
    mcp_app.add_middleware(IdentityMiddleware)
    return mcp_app


@server.tool()
def create_plan(name: str, salary: float, pay_frequency: str,
                current_contrib_pct: float, match_pct: float,
                match_cap_pct: float, true_up: bool = False) -> dict:
    """Create a 401(k) match analysis plan. Returns the summary incl. uncaptured
    match dollars; the full fix pack unlocks after the $99/year fee is paid."""
    owner = require_owner()
    if pay_frequency not in PAY_PERIODS:
        return {"error": f"pay_frequency must be one of {sorted(PAY_PERIODS)}"}
    if salary <= 0:
        return {"error": "salary must be positive"}
    import uuid
    from datetime import datetime, timezone
    result = calculate(salary, pay_frequency, current_contrib_pct,
                       match_pct, match_cap_pct, true_up)
    plan = {
        "id": uuid.uuid4().hex[:12],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {"name": sanitize_text(name), "salary": salary, "pay_frequency": pay_frequency,
                   "current_contrib_pct": current_contrib_pct, "match_pct": match_pct,
                   "match_cap_pct": match_cap_pct, "true_up": true_up},
        "result": result,
        "paid": False,
        "stripe_customer_id": None,
        "stripe_setup_intent_id": None,
        "stripe_payment_intent_id": None,
        "paid_at": None,
        "owner_id": owner,
    }
    save_plan(plan)
    return _summary(plan)


@server.tool()
def get_plan_summary(plan_id: str) -> dict:
    """Get the summary of an existing plan (uncaptured match $, paid status)."""
    owner = require_owner()
    plan = get_plan(plan_id, owner)
    if not plan:
        return {"error": "Plan not found"}
    return _summary(plan)


@server.tool()
def setup_billing(plan_id: str, email: str = "") -> dict:
    """Set up billing: create the Stripe customer and return a SetupIntent
    client_secret so the user can save a card for the $99/year fee."""
    owner = require_owner()
    plan = get_plan(plan_id, owner)
    if not plan:
        return {"error": "Plan not found"}
    if plan["paid"]:
        return {"error": "Plan already paid"}
    cust = setup_customer(plan["inputs"]["name"], email)
    if "error" in cust:
        return {"error": f"Stripe customer creation failed: {cust['error']}"}
    si = create_card_setup(cust["customer_id"],
                           idempotency_key=f"{CONNECTOR}-setup-{owner}-{plan_id}")
    if "error" in si:
        return {"error": f"Stripe SetupIntent failed: {si['error']}"}
    set_billing(plan_id, cust["customer_id"], si["setup_intent_id"], owner)
    return {"plan_id": plan_id, "stripe_customer_id": cust["customer_id"],
            "setup_intent_id": si["setup_intent_id"],
            "client_secret": si["client_secret"],
            "fee_disclosure": FEE_DISCLOSURE,
            "user_message": ("Card setup is ready — saving a card now costs nothing. "
                             + FEE_DISCLOSURE + " Ready to save your card?")}


@server.tool()
def pay_fee(plan_id: str) -> dict:
    """Charge the flat $99/year fee off-session against the saved card.
    Unlocks the fix pack. Idempotent: an already-paid plan errors, never double-charges."""
    owner = require_owner()
    import datetime
    plan = get_plan(plan_id, owner)
    if not plan:
        return {"error": "Plan not found"}
    if plan["paid"]:
        return {"error": "Plan already paid"}
    if not plan.get("stripe_customer_id"):
        return {"error": "Run setup_billing first to save a card"}
    res = charge_fee(plan["stripe_customer_id"],
                     idempotency_key=f"{CONNECTOR}-fee-{owner}-{plan_id}")
    if "error" in res:
        return {"error": f"Stripe charge failed: {res['error']}"}
    paid_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if not set_paid(plan_id, plan["stripe_customer_id"], res["payment_intent_id"], paid_at, owner):
        return {"error": "Plan already paid"}
    return {"plan_id": plan_id, "paid": True, "amount_dollars": ANNUAL_FEE_DOLLARS,
            "payment_intent_id": res["payment_intent_id"],
            "fee_disclosure": FEE_DISCLOSURE,
            "user_message": "All set — $99.00 for the year is paid, and your full fix plan is unlocked."}


@server.tool()
def get_fix_pack(plan_id: str) -> dict:
    """Return the full fix plan (recommended %, per-paycheck $, steps).
    Requires the $99/year fee to be paid."""
    owner = require_owner()
    plan = get_plan(plan_id, owner)
    if not plan:
        return {"error": "Plan not found"}
    if not plan["paid"]:
        return {"error": "Payment required: run setup_billing then pay_fee to unlock the fix plan."}
    return _pack(plan)


@server.tool()
def billing_status(plan_id: str) -> dict:
    """Poll billing state: card state + whether the $99/year fee is settled."""
    owner = require_owner()
    plan = get_plan(plan_id, owner)
    if not plan:
        return {"error": "Plan not found"}
    customer_id = plan["stripe_customer_id"]
    if not customer_id:
        status, card_state = "none", "none"
    elif plan["paid"]:
        status, card_state = "paid", "ready"
    else:
        status, card_state = "card_pending", "pending"
    return {"plan_id": plan_id, "billing_status": status, "card_state": card_state,
            "amount_dollars": ANNUAL_FEE_DOLLARS, "fee_disclosure": FEE_DISCLOSURE,
            "user_message": "The $99.00 yearly fee is paid — your fix plan is unlocked."
            if plan["paid"] else "No charge yet — your card state is: " + card_state + "."}


@server.tool()
def start_draft() -> dict:
    """Start the conversational intake (golden path). Returns the first question
    in user_message — ask it verbatim, then pass answers to answer_draft."""
    owner = require_owner()
    return _draft_question(_new_draft(owner_id=owner))


@server.tool()
def answer_draft(draft_id: str, answer: str) -> dict:
    """Answer the current draft question. Returns the next question in
    user_message, or the finished payoff summary when done."""
    owner = require_owner()
    draft = get_draft(draft_id, owner)
    if not draft:
        return {"error": "Draft not found"}
    return _answer_draft(draft, answer, owner)


@server.tool()
def disclaimer() -> dict:
    """Return the connector's not-financial-advice disclaimer."""
    owner = require_owner()
    return {"disclaimer": DISCLAIMER,
            "user_message": "Quick note: this is a financial-education tool, not financial advice — confirm anything with your plan administrator."}


if __name__ == "__main__":
    import os as _os

    import uvicorn

    port = int(_os.environ.get("MCP_PORT", "8579"))
    uvicorn.run(create_mcp_app(), host="127.0.0.1", port=port, log_level="warning")
