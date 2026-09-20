"""Subscription Slayer — MCP server (streamable HTTP).

Each tool mirrors a REST action and returns machine JSON plus a `user_message`
field: a warm, speakable sentence the agent can say verbatim in chat.
The agent is the UI — every flow must be fully demoable in a plain transcript.

Every tool resolves the calling user first (``require_owner()``) and scopes
all database access by that owner. The MCP ASGI app is built by
``create_mcp_app()``, which wraps the streamable-HTTP app in
``IdentityMiddleware`` — no identity means the tool raises IdentityError.
"""
import json as _json
import os
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from identity import IdentityMiddleware, require_owner  # noqa: E402
from app import (  # noqa: E402  (shares logic with the REST layer)
    cancel_pack_for,
    clean,
    get_conn,
    get_sub_or_404,
    money,
    nudge_for,
    row_to_dict,
)
import billing  # noqa: E402
import db  # noqa: E402
import scanner  # noqa: E402

sys.path.insert(0, str(Path.home() / "workspace/connectors/life-events"))
import events as life_events  # noqa: E402

server = MCPServer("subscription-slayer")


@server.tool(description="Scan Gmail for subscription receipts and detect recurring subscriptions. "
                         "source='fixtures' runs a deterministic demo scan without touching Gmail.")
def scan_subscriptions(source: str = "gmail", max_results: int = 100) -> dict:
    owner = require_owner()
    result = scanner.scan_and_detect(source=source, max_results=max(1, min(500, max_results)))
    if "error" in result:
        return {
            "error": result["error"],
            "user_message": ("I couldn't reach your Gmail to scan for subscriptions. "
                             "Connect it and I'll try again — or say the word and I'll run a demo scan instead."),
        }
    detected = result["subscriptions"]
    conn = get_conn()
    for s in detected:
        if s["amount"] is None:
            continue
        sub_id = db.upsert_subscription(
            conn, owner,
            merchant=s["merchant"], merchant_key=s["merchant_key"],
            amount=s["amount"], currency=s["currency"], frequency=s["frequency"],
            confidence=s["confidence"], first_seen=s["first_seen"],
            last_seen=s["last_seen"], occurrences=s["occurrences"],
        )
        if s["recurring"]:
            life_events.emit(
                "recurring_charge_detected",
                {"merchant": s["merchant"], "merchant_key": s["merchant_key"],
                 "amount": s["amount"], "currency": s["currency"],
                 "frequency": s["frequency"], "occurrences": s["occurrences"],
                 "subscription_id": sub_id},
                source="subscription-slayer",
            )
    conn.close()
    recurring = [s for s in detected if s["recurring"] and s["amount"] is not None]
    total = sum(s["amount"] * (12 if s["frequency"] == "monthly" else 1) for s in recurring)
    if recurring:
        top = max(recurring, key=lambda s: s["amount"] * (12 if s["frequency"] == "monthly" else 1))
        msg = (f"I found {len(recurring)} recurring subscription{'s' if len(recurring) != 1 else ''} "
               f"costing you about {money(total)} a year. {nudge_for(top)}")
    else:
        msg = "I scanned your receipts and didn't spot any recurring subscriptions this time."
    return {"detected": detected, "recurring_count": len(recurring),
            "estimated_annual_cost": round(total, 2), "user_message": msg}


@server.tool(description="List tracked subscriptions, optionally filtered by status "
                         "(new/reviewing/keep/cancel_requested/cancelled).")
def list_subscriptions(status: str | None = None) -> dict:
    owner = require_owner()
    valid = {"new", "reviewing", "keep", "cancel_requested", "cancelled"}
    if status is not None and status not in valid:
        return {"error": f"status must be one of {sorted(valid)}",
                "user_message": "I didn't recognise that status — try new, reviewing, keep, cancel_requested, or cancelled."}
    conn = get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM subscriptions WHERE owner_id = ? AND status = ? ORDER BY amount DESC",
            (owner, status)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM subscriptions WHERE owner_id = ? ORDER BY amount DESC",
            (owner,)).fetchall()
    conn.close()
    subs = [row_to_dict(r) for r in rows]
    if not subs:
        msg = "No subscriptions tracked yet. Run a scan and I'll list them again."
    else:
        total = sum(s["amount"] * (12 if s["frequency"] == "monthly" else 1) for s in subs)
        msg = (f"You're tracking {len(subs)} subscription{'s' if len(subs) != 1 else ''}, "
               f"about {money(total)} a year. Tell me which one to kill first.")
    return {"subscriptions": subs, "count": len(subs), "user_message": msg}


@server.tool(description="Update a subscription: set status, record the confirmed monthly "
                         "savings after cancelling, or add a note.")
def update_subscription(subscription_id: int, status: str | None = None,
                        savings_monthly: float | None = None, notes: str | None = None) -> dict:
    owner = require_owner()
    valid = {"new", "reviewing", "keep", "cancel_requested", "cancelled"}
    if status is not None and status not in valid:
        return {"error": f"status must be one of {sorted(valid)}",
                "user_message": "I didn't recognise that status — try new, reviewing, keep, cancel_requested, or cancelled."}
    if savings_monthly is not None and (savings_monthly < 0 or savings_monthly > 100000):
        return {"error": "savings_monthly must be between 0 and 100000",
                "user_message": "That savings amount doesn't look right — what was the monthly charge?"}
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM subscriptions WHERE id = ? AND owner_id = ?",
        (subscription_id, owner)).fetchone()
    if row is None:
        conn.close()
        return {"error": f"Subscription {subscription_id} not found.",
                "user_message": "I couldn't find that subscription — want me to list them again?"}
    sub = row_to_dict(row)
    updates: dict = {}
    if status is not None:
        updates["status"] = status
    if savings_monthly is not None:
        updates["savings_monthly"] = savings_monthly
    if notes is not None:
        updates["notes"] = clean(notes, 500)
    if not updates:
        conn.close()
        return {"error": "Nothing to update.",
                "user_message": "Tell me what to change — the status, the monthly savings, or a note."}
    if status == "cancelled" and savings_monthly is None and sub.get("savings_monthly") is None:
        conn.close()
        return {"error": "Marking cancelled needs savings_monthly.",
                "user_message": "How much were you paying per month? I'll log it as your savings."}
    conn.execute(f"UPDATE subscriptions SET {', '.join(f'{k} = ?' for k in updates)}, updated_at = ? "
                 "WHERE id = ? AND owner_id = ?",
                 (*updates.values(), db._utcnow(), subscription_id, owner))
    conn.commit()
    row = get_sub_or_404(conn, subscription_id, owner)
    conn.close()
    sub = row_to_dict(row)
    merchant = clean(sub["merchant"], 40)
    if sub["status"] == "cancelled":
        monthly = sub.get("savings_monthly") or 0
        msg = (f"Done — {merchant} is marked cancelled, saving you {money(monthly)} a month. "
               "Say 'confirm my savings' and I'll tally everything up (my $10-per-cancellation fee only applies if you confirm).")
    elif sub["status"] == "keep":
        msg = f"Got it — keeping {merchant}. I'll stop nudging you about this one."
    else:
        msg = f"{merchant} is now marked '{sub['status']}'."
    return {"subscription": sub, "user_message": msg}


@server.tool(description="Get the step-by-step cancellation pack (curated steps + direct link) for a subscription.")
def get_cancel_pack(subscription_id: int) -> dict:
    owner = require_owner()
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM subscriptions WHERE id = ? AND owner_id = ?",
        (subscription_id, owner)).fetchone()
    conn.close()
    if row is None:
        return {"error": f"Subscription {subscription_id} not found.",
                "user_message": "I couldn't find that subscription — want me to list them again?"}
    sub = row_to_dict(row)
    pack = cancel_pack_for(sub)
    merchant = clean(sub["merchant"], 40)
    first = " ".join(f"Step {i+1}: {clean(s, 140)}" for i, s in enumerate(pack["steps"][:2]))
    if pack["curated"] and pack["cancel_url"]:
        msg = (f"Here's how to cancel {merchant}: {first} "
               f"Open this link to jump straight there: {pack['cancel_url']} "
               "Once it's done, tell me the monthly amount and I'll log your savings.")
    else:
        msg = (f"No curated guide for {merchant} yet — here's the universal playbook: {first} "
               "Once it's done, tell me the monthly amount and I'll log your savings.")
    return {"cancel_pack": pack, "user_message": msg}


@server.tool(description="Set up billing: create the Stripe customer and a SetupIntent so the user can "
                         "save a card. No charge is taken. The honest fee disclosure is returned first. "
                         "Returns an error if billing is already set up.")
def setup_billing(name: str, email: str = "") -> dict:
    owner = require_owner()
    name = clean(name, 100)
    email = clean(email, 200)
    conn = get_conn()
    existing = conn.execute("SELECT * FROM billing WHERE owner_id = ?", (owner,)).fetchone()
    if existing is not None:
        conn.close()
        return {"error": "Billing is already set up — no need to do it twice.",
                "user_message": "Billing is already set up — no need to do it twice."}
    customer = billing.setup_customer(name, email=email)
    if "error" in customer:
        conn.close()
        return {"error": f"Stripe customer creation failed: {customer['error']}",
                "user_message": "I hit a snag setting up billing on Stripe's side. Let's try again in a moment."}
    setup = billing.create_card_setup(
        customer["customer_id"],
        idempotency_key=f"{billing.CONNECTOR}-setup-{owner}-billing")
    if "error" in setup:
        conn.close()
        return {"error": f"Stripe SetupIntent failed: {setup['error']}",
                "user_message": "I hit a snag setting up billing on Stripe's side. Let's try again in a moment."}
    conn.execute("INSERT OR REPLACE INTO billing (owner_id, customer_id, setup_intent_id, created_at) VALUES (?,?,?,?)",
                 (owner, customer["customer_id"], setup.get("setup_intent_id"), db._utcnow()))
    conn.commit()
    conn.close()
    return {
        "customer_id": customer["customer_id"],
        "client_secret": setup["client_secret"],
        "fee_disclosure": billing.FEE_DISCLOSURE,
        "user_message": ("Your card is ready to be saved — but listen first: "
                         + billing.FEE_DISCLOSURE + " Saving a card charges you nothing today."),
    }


@server.tool(description="Confirm savings for cancelled subscriptions. This charges $10 per completed "
                         "cancellation off-session. Only call after the user explicitly confirms their savings.")
def confirm_savings(subscription_ids: list[int]) -> dict:
    owner = require_owner()
    conn = get_conn()
    rows = [conn.execute(
        "SELECT * FROM subscriptions WHERE id = ? AND owner_id = ?", (sid, owner)).fetchone()
        for sid in subscription_ids]
    missing = [sid for sid, r in zip(subscription_ids, rows) if r is None]
    if missing:
        conn.close()
        return {"error": f"Subscriptions not found: {missing}",
                "user_message": "I couldn't find one of those subscriptions — want me to list them again?"}
    subs = [row_to_dict(r) for r in rows]
    bad = [s["id"] for s in subs if s["status"] != "cancelled" or not (s.get("savings_monthly") or 0) > 0]
    if bad:
        conn.close()
        return {"error": f"Subscriptions {bad} must be cancelled with a positive savings_monthly first.",
                "user_message": "Those need to be marked cancelled with a monthly amount before I can confirm savings."}
    already = [s["id"] for s in subs if s["savings_confirmed"]]
    if already:
        conn.close()
        return {"error": f"Subscriptions {already} already confirmed (no double charge).",
                "user_message": "Those savings were already confirmed — no double charge, don't worry."}
    billing_row = conn.execute("SELECT * FROM billing WHERE owner_id = ?", (owner,)).fetchone()
    if billing_row is None:
        conn.close()
        return {"error": "No billing on file.",
                "user_message": "I need a card on file first — say 'set up billing' and we'll do that before any charge."}
    customer_id = billing_row["customer_id"]
    total = billing.first_year_savings(subs)  # informational only; the fee is flat
    n = len(subs)
    fee = billing.fee_cents(n)
    if fee <= 0:
        conn.close()
        return {"error": "No confirmed cancellations; nothing to charge.",
                "user_message": "The confirmed cancellations came out to zero, so there's nothing to charge."}
    desc = (f"Subscription Slayer fee: ${billing.FEE_PER_CANCELLATION_CENTS / 100:.0f} x {n} "
            f"confirmed cancellation{'s' if n != 1 else ''}")
    record_id = "subs-" + "-".join(str(sid) for sid in sorted(subscription_ids))
    charge = billing.charge_fee(
        customer_id, fee, desc,
        idempotency_key=f"{billing.CONNECTOR}-fee-{owner}-{record_id}")
    if "error" in charge:
        conn.close()
        return {"error": f"Charge failed: {charge['error']}",
                "user_message": "The charge didn't go through — your card wasn't billed. Let's try again in a moment."}
    for s in subs:
        conn.execute("UPDATE subscriptions SET savings_confirmed = 1, updated_at = ? WHERE id = ? AND owner_id = ?",
                     (db._utcnow(), s["id"], owner))
    conn.execute(
        "INSERT INTO charges (owner_id, customer_id, amount_cents, currency, description, subscription_ids,"
        " payment_intent_id, status, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (owner, customer_id, fee, "usd", clean(desc, 200), _json.dumps(subscription_ids),
         charge.get("payment_intent_id"), "succeeded", db._utcnow()),
    )
    conn.commit()
    conn.close()
    return {
        "first_year_savings": total,
        "cancellations_confirmed": n,
        "fee_per_cancellation_usd": billing.FEE_PER_CANCELLATION_CENTS / 100,
        "fee_charged": round(fee / 100, 2),
        "payment_intent_id": charge.get("payment_intent_id"),
        "user_message": (f"Savings confirmed! You locked in {money(total)} of first-year savings, "
                         f"and my fee ({money(fee / 100)} for {n} cancellation{'s' if n != 1 else ''}) is settled. "
                         f"That's {money(total - fee / 100)} staying in your pocket every year. Well done."),
    }


def create_mcp_app():
    """Build the MCP ASGI app with the identity middleware.

    Every tool call passes through :class:`identity.IdentityMiddleware`,
    which resolves the platform user into a contextvar that tools read via
    ``require_owner()``. No identity -> the tool raises IdentityError.
    """
    app = server.streamable_http_app(streamable_http_path="/mcp")
    app.add_middleware(IdentityMiddleware)
    return app


def main() -> None:
    import argparse
    import uvicorn

    ap = argparse.ArgumentParser(description="Subscription Slayer MCP server (streamable HTTP)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("SLAYER_MCP_PORT", "8573")))
    ap.add_argument("--host", default=os.environ.get("SLAYER_HOST", "127.0.0.1"))
    args = ap.parse_args()
    uvicorn.run(create_mcp_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
