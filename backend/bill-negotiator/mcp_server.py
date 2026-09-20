"""Bill-negotiator MCP server: one tool per action.

Connects via Muse's Custom connector flow (public streamable-HTTP endpoint).
Agent-native: every tool output carries a `user_message` — a warm, ready-to-speak
sentence the agent can say verbatim. No app screen needed.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from src.db import init_db, create_case, get_case, update_case, claim_case, serialize
from src.scripts import script_pack, list_providers, clean_text
from src.billing import (setup_customer, create_card_setup, charge_fee, fee_cents,
                         FEE_RATE, CONNECTOR)
from src.identity import IdentityMiddleware, require_owner

LIFE_EVENTS_DIR = Path.home() / "workspace/connectors/life-events"
if str(LIFE_EVENTS_DIR) not in sys.path:
    sys.path.insert(0, str(LIFE_EVENTS_DIR))
try:
    import events as life_events
except Exception:
    life_events = None

init_db()

server = MCPServer("bill-negotiator")


def create_mcp_app():
    from src.identity import IdentityMiddleware
    app = server.streamable_http_app(streamable_http_path="/mcp")
    app.add_middleware(IdentityMiddleware)
    return app


def _owned_case(case_id: str, owner: str):
    """(case, None) for the owner's case — claiming an ownerless life-event
    draft for them — or (None, error_dict) which reads as 404 to anyone else."""
    case = get_case(case_id, owner)
    if case:
        return case, None
    claimed = claim_case(case_id, owner)
    if claimed:
        return claimed, None
    return None, {"error": "Unknown case",
                  "user_message": "Hmm, I can't find that case — mind checking the ID?"}

FEE_DISCLOSURE = ("You will be charged 35% of your documented bill savings, "
                  "only if you confirm the new lower bill. No charge otherwise.")


def _money(x: float) -> str:
    return f"${x:,.2f}" if x != int(x) else f"${int(x)}"


@server.tool()
def create_negotiation_case(provider: str, service_type: str, current_monthly_bill: float,
                            promo_end_date: str | None = None,
                            account_tenure_months: int | None = None,
                            user_name: str | None = None,
                            prior_monthly_bill: float | None = None) -> dict:
    """Open a bill-negotiation case (internet/cable/phone provider)."""
    owner = require_owner()
    case = create_case(clean_text(provider), clean_text(service_type, max_len=60),
                       current_monthly_bill, promo_end_date, account_tenure_months,
                       clean_text(user_name) if user_name else None,
                       owner_id=owner)
    emitted = None
    if life_events and prior_monthly_bill and current_monthly_bill > prior_monthly_bill:
        try:
            life_events.emit("bill_spike", {
                "provider": case["provider"], "service_type": case["service_type"],
                "prior_monthly_bill": prior_monthly_bill,
                "current_monthly_bill": current_monthly_bill,
                "case_id": case["id"]}, source="bill-negotiator")
            emitted = "bill_spike"
        except Exception:
            pass
    if emitted:
        user_message = (f"Whoa — your {case['provider']} bill jumped from {_money(prior_monthly_bill)} "
                        f"to {_money(current_monthly_bill)}. I've logged it and your script pack is ready. "
                        "One tap to see it.")
    else:
        user_message = (f"Case opened for your {case['provider']} {case['service_type']} bill "
                        f"({_money(current_monthly_bill)}/mo). Your script pack is ready — one tap "
                        "and you're calling with a plan.")
    return {"case_id": case["id"], "event_emitted": emitted,
            "user_message": user_message, **serialize(case)}


@server.tool()
def get_negotiation_script(case_id: str) -> dict:
    """Get the per-provider negotiation script pack (call + chat scripts, talking points)."""
    owner = require_owner()
    case, err = _owned_case(case_id, owner)
    if err:
        return err
    script = script_pack(case["provider"], case["service_type"],
                         case["current_monthly_bill"], case["account_tenure_months"])
    return {"case_id": case_id,
            "user_message": (f"Your {script['provider']} game plan is ready: call the retention team, "
                             "say your promo ended, mention a competitor's price, and ask them to match it. "
                             "The exact words are in the script — you've got this."),
            "script": script}


@server.tool()
def report_negotiation_outcome(case_id: str, success: bool, new_monthly_bill: float | None = None,
                               months_locked: int | None = None) -> dict:
    """Record the user's reported negotiation outcome; computes documented savings."""
    owner = require_owner()
    case, err = _owned_case(case_id, owner)
    if err:
        return err
    old = case["current_monthly_bill"]
    if not success or new_monthly_bill is None:
        update_case(case_id, owner, new_monthly_bill=None, months_locked=None, savings=0.0,
                    outcome_reported_at=datetime.now(timezone.utc).isoformat())
        return {"case_id": case_id, "success": False,
                "user_message": "No luck this time — and no fee owed. I'll keep an eye out for "
                                "your next promo window so we can try again."}
    if new_monthly_bill >= old:
        return {"error": f"new_monthly_bill must be lower than {_money(old)} to count as savings",
                "user_message": f"That new bill ({_money(new_monthly_bill)}) isn't lower than what you're "
                                f"paying now ({_money(old)}). Did the provider quote a different number?"}
    months = min(months_locked or 1, 12)
    savings = round((old - new_monthly_bill) * months, 2)
    update_case(case_id, owner, new_monthly_bill=new_monthly_bill, months_locked=months,
                savings=savings, outcome_reported_at=datetime.now(timezone.utc).isoformat())
    return {"case_id": case_id, "success": True, "documented_savings": savings,
            "monthly_saving": round(old - new_monthly_bill, 2),
            "months_locked": months, "estimated_fee_cents": fee_cents(savings),
            "user_message": (f"That's {_money(savings)} in documented savings over {months} months — "
                             f"from {_money(old)} down to {_money(new_monthly_bill)}/mo. My fee would be "
                             f"{_money(savings * FEE_RATE)} (35%), and only if you confirm this bill.")}


@server.tool()
def setup_negotiation_billing(case_id: str) -> dict:
    """Create the Stripe customer + card SetupIntent for the 35% savings fee."""
    owner = require_owner()
    case, err = _owned_case(case_id, owner)
    if err:
        return err
    if case.get("stripe_customer_id"):
        return {"error": "Billing already set up for this case",
                "user_message": "Your card's already on file for this one — nothing more to do."}
    name = case.get("user_name") or f"Bill negotiation case {case_id}"
    setup_key = f"{CONNECTOR}-setup-{owner}-{case_id}"
    cust = setup_customer(name, idempotency_key=setup_key)
    if "error" in cust:
        return {"error": f"Stripe customer creation failed: {cust['error']}",
                "user_message": "I couldn't set up billing just now — let's try again in a minute."}
    setup = create_card_setup(cust["customer_id"], idempotency_key=setup_key)
    if "error" in setup:
        return {"error": f"Stripe card setup failed: {setup['error']}",
                "user_message": "I couldn't set up billing just now — let's try again in a minute."}
    update_case(case_id, owner, stripe_customer_id=cust["customer_id"],
                setup_intent_id=setup.get("setup_intent_id"), billing_status="card_pending")
    return {"case_id": case_id, "stripe_customer_id": cust["customer_id"],
            "client_secret": setup["client_secret"],
            "fee_disclosure": FEE_DISCLOSURE,
            "user_message": FEE_DISCLOSURE + " Save your card to continue — nothing is charged now.",
            "note": "Collect the user's card against this SetupIntent. The 35% fee is "
                    "charged off-session ONLY after savings are confirmed."}


@server.tool()
def charge_negotiation_fee(case_id: str) -> dict:
    """Charge the 35% fee off-session on user-confirmed documented savings."""
    owner = require_owner()
    case, err = _owned_case(case_id, owner)
    if err:
        return err
    if case.get("billing_status") == "fee_charged":
        return {"error": "Fee already charged for this case",
                "user_message": "Already charged for this one — you're all set."}
    if case.get("new_monthly_bill") is None:
        if case.get("outcome_reported_at"):
            update_case(case_id, owner, billing_status="no_fee_no_savings")
            return {"case_id": case_id, "documented_savings": 0.0, "fee_cents": 0,
                    "status": "no_fee_no_savings",
                    "user_message": "No documented savings, so no charge. Nothing owed."}
        return {"error": "No successful outcome reported yet",
                "user_message": "Tell me the new bill first — I can't charge until you've confirmed the savings."}
    savings = case.get("savings") or 0.0
    if savings <= 0:
        update_case(case_id, owner, billing_status="no_fee_no_savings")
        return {"case_id": case_id, "documented_savings": savings, "fee_cents": 0,
                "status": "no_fee_no_savings",
                "user_message": "No documented savings, so no charge. Nothing owed."}
    customer_id = case.get("stripe_customer_id")
    if not customer_id:
        return {"error": "Billing not set up: run setup_negotiation_billing and save a card first",
                "user_message": "We need a card on file first — one quick setup, then I can charge the fee."}
    cents = fee_cents(savings)
    result = charge_fee(customer_id, cents,
                        f"Bill negotiation fee ({int(FEE_RATE*100)}% of {_money(savings)} documented savings) - case {case_id}",
                        idempotency_key=f"{CONNECTOR}-fee-{owner}-{case_id}")
    if "error" in result:
        update_case(case_id, owner, billing_status="fee_failed")
        return {"error": f"Fee charge failed: {result['error']}",
                "user_message": "The charge didn't go through — your card may need attention. No worries, I'll retry when you say."}
    update_case(case_id, owner, billing_status="fee_charged", fee_cents=cents,
                payment_intent_id=result["payment_intent_id"])
    return {"case_id": case_id, "documented_savings": savings, "fee_rate": FEE_RATE,
            "fee_cents": cents, "payment_intent_id": result["payment_intent_id"],
            "status": "fee_charged",
            "user_message": f"Done — {_money(cents / 100)} charged (35% of {_money(savings)} in savings). "
                            "That's money back in your pocket every month from here on."}


@server.tool()
def receive_life_event(event_type: str, payload: dict | None = None) -> dict:
    """Receive a shared life-events bus event. bill_spike creates a draft case and returns the proactive nudge."""
    owner = require_owner()
    p = payload or {}
    if event_type != "bill_spike":
        return {"received": True, "handled": False,
                "user_message": f"Got a '{clean_text(event_type, max_len=40)}' event — nothing for me "
                                "to do with it, so I'm standing by."}
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
                       current_monthly_bill=current, promo_end_date=p.get("promo_end_date"),
                       account_tenure_months=tenure,
                       user_name=clean_text(str(p.get("user_name"))) if p.get("user_name") else None,
                       owner_id=owner)
    script = script_pack(provider, service_type, current, case["account_tenure_months"])
    if prior and current and current > prior:
        user_message = (f"Your {service_type} bill jumped from {_money(prior)} to {_money(current)} — "
                        "your promo expired. People are getting it back down with one call. "
                        "Want the script? One tap.")
    else:
        user_message = (f"Your {provider} {service_type} bill changed — I've drafted your case and "
                        "pulled the negotiation script. Want it? One tap.")
    return {"received": True, "handled": True, "event_type": "bill_spike",
            "case_id": case["id"], "user_message": user_message, "script": script}


@server.tool()
def list_supported_providers() -> dict:
    """List providers with researched negotiation scripts."""
    owner = require_owner()  # noqa: F841 - identity enforced for all tenant tools
    providers = list_providers()
    return {"providers": providers,
            "user_message": f"I've got negotiation scripts ready for {len(providers)} providers — "
                            "just tell me yours and your current bill."}


def main():
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser(description="bill-negotiator MCP server (streamable-http)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(__import__("os").environ.get("MCP_PORT", "8574")))
    args = parser.parse_args()
    uvicorn.run(create_mcp_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
