"""Subscription Slayer — REST API.

The agent is the UI: every endpoint returns a `user_message` field alongside
the machine JSON — a warm, ready-to-speak sentence the agent can say verbatim
inside a plain chat transcript. No screens, no forms; every flow is a short
conversation.

Golden path: proactive nudge -> one tap -> done.
"""
import html
import json
import os
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import billing
import db
import scanner
from identity import IdentityMiddleware, IdentityError, require_owner
from limits import RateLimitMiddleware, BodySizeLimitMiddleware

# Shared life-event bus (suite compounding: one intelligence, ten connectors).
sys.path.insert(0, str(Path.home() / "workspace/connectors/life-events"))
import events as life_events

_PROD = os.environ.get("ENV") == "production"
_PUBLIC_DOCS = os.environ.get("PUBLIC_DOCS") == "1"

app = FastAPI(
    title="Subscription Slayer",
    version="0.2.0",
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

STATUSES = {"new", "reviewing", "keep", "cancel_requested", "cancelled"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£"}


def money(amount: float, currency: str = "USD") -> str:
    symbol = SYMBOLS.get(currency)
    if symbol:
        return f"{symbol}{amount:,.2f}"
    return f"{amount:,.2f} {currency}"


def clean(text: str, limit: int = 200) -> str:
    """Sanitize untrusted user/email text: no control chars, bounded length.

    JSON is safe by construction, but tool outputs may be rendered as HTML
    or spoken aloud, so escape markup and strip control characters.
    """
    text = (text or "")[:limit]
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    return html.escape(text.strip())


def row_to_dict(row) -> dict:
    d = dict(row)
    d["savings_confirmed"] = bool(d.get("savings_confirmed"))
    return d


def get_conn():
    return db.get_db()


def get_sub_or_404(conn, sub_id: int, owner: str):
    row = conn.execute(
        "SELECT * FROM subscriptions WHERE id = ? AND owner_id = ?", (sub_id, owner)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Subscription {sub_id} not found.")
    return row


def _billing_row(conn, owner: str):
    return conn.execute("SELECT * FROM billing WHERE owner_id = ?", (owner,)).fetchone()


def _card_state(billing_row) -> str:
    """Pollable card-save state for GET /api/billing/status."""
    if billing_row is None or not billing_row["customer_id"]:
        return "none"
    return "pending"  # a saved customer row means a card is on file for the fee


def load_guides() -> dict:
    path = BASE_DIR / "cancel_guides.json"
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def cancel_pack_for(sub: dict) -> dict:
    """Curated cancellation pack for a merchant, or the generic fallback."""
    guides = load_guides()
    guide = guides.get("merchants", {}).get(sub["merchant_key"])
    if guide:
        return {
            "merchant": sub["merchant"],
            "curated": True,
            "cancel_url": guide["cancel_url"],
            "steps": guide["steps"],
            "difficulty": guide.get("difficulty", "medium"),
            "notes": guide.get("notes", ""),
        }
    fallback = guides.get("fallback", {})
    merchant = clean(sub["merchant"], 60)
    return {
        "merchant": sub["merchant"],
        "curated": False,
        "cancel_url": "",
        "steps": [
            f"Log in to your {merchant} account on their website or app.",
            "Open Settings, then Billing / Subscription / Membership.",
            "Choose Cancel (look for small print or a 'Manage' link).",
            "Screenshot the confirmation page or save the confirmation email.",
        ],
        "difficulty": "unknown",
        "notes": fallback.get("notes", ""),
    }


def nudge_for(sub: dict) -> str:
    """The proactive nudge: warm, specific, realistic numbers, one tap."""
    merchant = clean(sub["merchant"], 40)
    amount = float(sub.get("amount") or 0)
    occ = int(sub.get("occurrences") or 0)
    annual = amount * (12 if sub.get("frequency") == "monthly" else 1)
    paid_line = f"You've paid {money(amount, sub.get('currency', 'USD'))} to {merchant}"
    if occ >= 2:
        paid_line += f" for {occ} billing cycles"
    paid_line += "."
    return (f"{paid_line} That's about {money(annual, sub.get('currency', 'USD'))} a year. "
            f"Want me to walk you through cancelling? One tap.")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ScanRequest(BaseModel):
    source: str = Field(default="gmail", description="'gmail' or 'fixtures'")
    max: int = Field(default=100, ge=1, le=500)

    @field_validator("source")
    @classmethod
    def _source_ok(cls, v: str) -> str:
        if v not in ("gmail", "fixtures"):
            raise ValueError("source must be 'gmail' or 'fixtures'")
        return v


class SubscriptionUpdate(BaseModel):
    status: str | None = None
    savings_monthly: float | None = Field(default=None, ge=0, le=100000)
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("status")
    @classmethod
    def _status_ok(cls, v: str | None) -> str | None:
        if v is not None and v not in STATUSES:
            raise ValueError(f"status must be one of {sorted(STATUSES)}")
        return v


class BillingSetupRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(default="", max_length=200)


class SavingsConfirmRequest(BaseModel):
    subscription_ids: list[int] = Field(..., min_length=1, max_length=100)


class LifeEventRequest(BaseModel):
    event_type: str = Field(..., min_length=1, max_length=60)
    payload: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "subscription-slayer",
        "version": "0.2.0",
        "user_message": "Subscription Slayer is awake and ready to hunt down your recurring charges.",
    }


@app.post("/api/scan")
def scan(req: ScanRequest):
    owner = require_owner()
    result = scanner.scan_and_detect(source=req.source, max_results=req.max)
    if "error" in result:
        status = 503 if result["error"] == "gmail_not_connected" else 400
        return JSONResponse(
            status_code=status,
            content={
                "error": result["error"],
                "detail": result.get("detail", ""),
                "user_message": ("I couldn't reach your Gmail to scan for subscriptions. "
                                 "Connect it and I'll try again — or say the word and I'll run a demo scan instead."),
            },
        )
    detected = result["subscriptions"]
    conn = get_conn()
    saved_ids = []
    emitted = []
    for s in detected:
        if s["amount"] is None:
            continue  # can't price it, can't nudge about it
        sub_id = db.upsert_subscription(
            conn, owner,
            merchant=s["merchant"], merchant_key=s["merchant_key"],
            amount=s["amount"], currency=s["currency"], frequency=s["frequency"],
            confidence=s["confidence"], first_seen=s["first_seen"],
            last_seen=s["last_seen"], occurrences=s["occurrences"],
        )
        saved_ids.append(sub_id)
        if s["recurring"]:
            evt = life_events.emit(
                "recurring_charge_detected",
                {"merchant": s["merchant"], "merchant_key": s["merchant_key"],
                 "amount": s["amount"], "currency": s["currency"],
                 "frequency": s["frequency"], "occurrences": s["occurrences"],
                 "subscription_id": sub_id},
                source="subscription-slayer",
            )
            emitted.append(evt)
    conn.close()

    recurring = [s for s in detected if s["recurring"] and s["amount"] is not None]
    total_annual = sum(
        s["amount"] * (12 if s["frequency"] == "monthly" else 1) for s in recurring
    )
    if recurring:
        top = max(recurring, key=lambda s: s["amount"] * (12 if s["frequency"] == "monthly" else 1))
        msg = (f"I found {len(recurring)} recurring subscription{'s' if len(recurring) != 1 else ''} "
               f"costing you about {money(total_annual)} a year. "
               f"{nudge_for(top)}")
    else:
        msg = ("I scanned your receipts and didn't spot any recurring subscriptions "
               "this time. Nice — or your receipts live somewhere I didn't look.")
    return {
        "scanned_source": req.source,
        "detected": detected,
        "recurring_count": len(recurring),
        "estimated_annual_cost": round(total_annual, 2),
        "events_emitted": len(emitted),
        "user_message": msg,
    }


@app.get("/api/subscriptions")
def list_subscriptions(status: str | None = None):
    owner = require_owner()
    if status is not None and status not in STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {sorted(STATUSES)}")
    conn = get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM subscriptions WHERE owner_id = ? AND status = ? ORDER BY amount DESC",
            (owner, status),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM subscriptions WHERE owner_id = ? ORDER BY amount DESC", (owner,)
        ).fetchall()
    conn.close()
    subs = [row_to_dict(r) for r in rows]
    if not subs:
        msg = "No subscriptions tracked yet. Run a scan and I'll list everything I find."
    else:
        total = sum(s["amount"] * (12 if s["frequency"] == "monthly" else 1) for s in subs)
        msg = (f"You're tracking {len(subs)} subscription{'s' if len(subs) != 1 else ''}, "
               f"about {money(total)} a year. Tell me which one to kill first.")
    return {"subscriptions": subs, "count": len(subs), "user_message": msg}


@app.patch("/api/subscriptions/{sub_id}")
def update_subscription(sub_id: int, req: SubscriptionUpdate):
    owner = require_owner()
    conn = get_conn()
    row = get_sub_or_404(conn, sub_id, owner)
    sub = row_to_dict(row)
    updates: dict = {}
    if req.status is not None:
        updates["status"] = req.status
    if req.savings_monthly is not None:
        updates["savings_monthly"] = req.savings_monthly
    if req.notes is not None:
        updates["notes"] = clean(req.notes, 500)
    if not updates:
        conn.close()
        raise HTTPException(status_code=422, detail="Nothing to update: pass status, savings_monthly, or notes.")
    if req.status == "cancelled" and req.savings_monthly is None and sub.get("savings_monthly") is None:
        conn.close()
        raise HTTPException(
            status_code=422,
            detail="Marking cancelled needs savings_monthly so savings can be tracked "
                   "(e.g. {\"status\": \"cancelled\", \"savings_monthly\": 14.99}).",
        )
    set_clause = ", ".join(f"{k} = ?" for k in updates) + ", updated_at = ?"
    conn.execute(f"UPDATE subscriptions SET {set_clause} WHERE id = ? AND owner_id = ?",
                 (*updates.values(), db._utcnow(), sub_id, owner))
    conn.commit()
    row = get_sub_or_404(conn, sub_id, owner)
    conn.close()
    sub = row_to_dict(row)
    merchant = clean(sub["merchant"], 40)
    if sub["status"] == "cancelled":
        monthly = sub.get("savings_monthly") or 0
        msg = (f"Done — {merchant} is marked cancelled, saving you {money(monthly)} a month. "
               f"Say 'confirm my savings' and I'll tally everything up (my $10-per-cancellation fee only applies if you confirm).")
    elif sub["status"] == "keep":
        msg = f"Got it — keeping {merchant}. I'll stop nudging you about this one."
    elif sub["status"] == "cancel_requested":
        msg = f"{merchant} is queued for cancellation. Pull up the cancel pack and I'll walk you through it."
    else:
        msg = f"{merchant} is now marked '{sub['status']}'."
    return {"subscription": sub, "user_message": msg}


@app.get("/api/subscriptions/{sub_id}/cancel-pack")
def get_cancel_pack(sub_id: int):
    owner = require_owner()
    conn = get_conn()
    row = get_sub_or_404(conn, sub_id, owner)
    conn.close()
    sub = row_to_dict(row)
    pack = cancel_pack_for(sub)
    merchant = clean(sub["merchant"], 40)
    first_steps = " ".join(f"Step {i+1}: {clean(s, 140)}" for i, s in enumerate(pack["steps"][:2]))
    if pack["curated"] and pack["cancel_url"]:
        msg = (f"Here's how to cancel {merchant}: {first_steps} "
               f"Open this link to jump straight there: {pack['cancel_url']} "
               f"Once it's done, tell me the monthly amount you were paying and I'll log your savings.")
    else:
        msg = (f"I don't have a curated guide for {merchant} yet, so here's the universal playbook: {first_steps} "
               f"Once it's done, tell me the monthly amount you were paying and I'll log your savings.")
    return {"cancel_pack": pack, "user_message": msg}


@app.post("/api/billing/setup")
def setup_billing(req: BillingSetupRequest):
    """Create the Stripe customer + SetupIntent. NO charge is taken here.

    The honest fee disclosure is returned in the response BEFORE the user
    saves a card, per the legal requirements for Meta review.
    Repeated setup returns 400 instead of orphaning a Stripe customer.
    """
    owner = require_owner()
    conn = get_conn()
    if _billing_row(conn, owner) is not None:
        conn.close()
        raise HTTPException(
            status_code=400,
            detail="Billing is already set up — no need to do it twice.")
    name = clean(req.name, 100)
    email = clean(req.email, 200)
    customer = billing.setup_customer(name, email=email)
    if "error" in customer:
        conn.close()
        raise HTTPException(status_code=502, detail=f"Stripe customer creation failed: {customer['error']}")
    setup = billing.create_card_setup(
        customer["customer_id"],
        idempotency_key=f"{billing.CONNECTOR}-setup-{owner}-billing")
    if "error" in setup:
        conn.close()
        raise HTTPException(status_code=502, detail=f"Stripe SetupIntent failed: {setup['error']}")

    conn.execute(
        "INSERT OR REPLACE INTO billing (owner_id, customer_id, setup_intent_id, created_at) VALUES (?,?,?,?)",
        (owner, customer["customer_id"], setup.get("setup_intent_id"), db._utcnow()),
    )
    conn.commit()
    conn.close()
    return {
        "customer_id": customer["customer_id"],
        "client_secret": setup["client_secret"],
        "setup_intent_id": setup.get("setup_intent_id"),
        "fee_disclosure": billing.FEE_DISCLOSURE,
        "user_message": ("Your card is ready to be saved — but listen first: "
                         + billing.FEE_DISCLOSURE +
                         " Saving a card charges you nothing today."),
    }


@app.get("/api/billing/status")
def billing_status():
    """Pollable card-save + billing state for the caller."""
    owner = require_owner()
    conn = get_conn()
    row = _billing_row(conn, owner)
    conn.close()
    billing_status = "card_pending" if row is not None else "none"
    card_state = _card_state(row)
    state_msg = {
        "none": "No card on file yet — set up billing when you're ready.",
        "pending": "Your card is saved and ready. Nothing is charged until you confirm your savings.",
    }.get(card_state, "Billing state unknown — ask me to check.")
    return {"billing_status": billing_status,
            "card_state": card_state,
            "fee_disclosure": billing.FEE_DISCLOSURE,
            "user_message": state_msg}


@app.post("/api/savings/confirmed")
def confirm_savings(req: SavingsConfirmRequest):
    """User-confirmed cancellations trigger the $10-per-cancellation off-session fee charge."""
    owner = require_owner()
    conn = get_conn()
    rows = []
    for sid in req.subscription_ids:
        rows.append(get_sub_or_404(conn, sid, owner))
    subs = [row_to_dict(r) for r in rows]

    bad = [s["id"] for s in subs
           if s["status"] != "cancelled" or not (s.get("savings_monthly") or 0) > 0]
    if bad:
        conn.close()
        raise HTTPException(
            status_code=422,
            detail=f"Subscriptions {bad} must be marked 'cancelled' with a positive "
                   "savings_monthly before savings can be confirmed.",
        )
    already = [s["id"] for s in subs if s["savings_confirmed"]]
    if already:
        conn.close()
        raise HTTPException(
            status_code=409,
            detail=f"Subscriptions {already} already had savings confirmed (no double charge).",
        )

    billing_row = _billing_row(conn, owner)
    if billing_row is None:
        conn.close()
        raise HTTPException(
            status_code=409,
            detail="No billing on file. Run POST /api/billing/setup and save a card first.",
        )
    customer_id = billing_row["customer_id"]

    total = billing.first_year_savings(subs)  # informational only; the fee is flat
    n = len(subs)
    fee = billing.fee_cents(n)
    if fee <= 0:
        conn.close()
        raise HTTPException(status_code=422, detail="No confirmed cancellations; nothing to charge.")

    desc = (f"Subscription Slayer fee: ${billing.FEE_PER_CANCELLATION_CENTS / 100:.0f} x {n} "
            f"confirmed cancellation{'s' if n != 1 else ''}")
    record_id = "subs-" + "-".join(str(sid) for sid in sorted(req.subscription_ids))
    charge = billing.charge_fee(
        customer_id, fee, desc,
        idempotency_key=f"{billing.CONNECTOR}-fee-{owner}-{record_id}")
    if "error" in charge:
        conn.execute(
            "INSERT INTO charges (owner_id, customer_id, amount_cents, currency, description, subscription_ids, status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (owner, customer_id, fee, "usd", clean(desc, 200),
             json.dumps(req.subscription_ids), "failed", db._utcnow()),
        )
        conn.commit()
        conn.close()
        raise HTTPException(status_code=502, detail=f"Charge failed: {charge['error']}")

    for s in subs:
        conn.execute("UPDATE subscriptions SET savings_confirmed = 1, updated_at = ? WHERE id = ? AND owner_id = ?",
                     (db._utcnow(), s["id"], owner))
    conn.execute(
        "INSERT INTO charges (owner_id, customer_id, amount_cents, currency, description, subscription_ids,"
        " payment_intent_id, status, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (owner, customer_id, fee, "usd", clean(desc, 200), json.dumps(req.subscription_ids),
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
        "subscription_ids": req.subscription_ids,
        "user_message": (f"Savings confirmed! You locked in {money(total)} of first-year savings, "
                         f"and my fee ({money(fee / 100)} for {n} cancellation{'s' if n != 1 else ''}) is settled. "
                         f"That's {money(total - fee / 100)} staying in your pocket every year. Well done."),
    }


@app.post("/api/life-events")
def receive_life_event(req: LifeEventRequest):
    """Bus receiver: turn a fanned-out life event into a draft case + nudge.

    Service-key authenticated (see IdentityMiddleware) — there is no user
    owner here, so created subscriptions store owner_id=NULL.

    Contract: pre-fill every field the payload provides, return the proactive
    nudge as `user_message`.
    """
    if req.event_type != "recurring_charge_detected":
        return {
            "event_type": req.event_type,
            "handled": False,
            "user_message": "Got it — that event isn't one I act on, so I'll stay quiet.",
        }
    payload = req.payload or {}
    merchant = clean(str(payload.get("merchant", "a subscription")), 40)
    amount = payload.get("amount") or 0
    currency = clean(str(payload.get("currency", "USD")), 8)
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        amount = 0.0
    occurrences = int(payload.get("occurrences") or 0)
    frequency = clean(str(payload.get("frequency", "monthly")), 16)
    merchant_key = clean(str(payload.get("merchant_key") or f"merchant_{merchant.lower().replace(' ', '_')}"), 40)

    sub_id = None
    if amount > 0:
        conn = get_conn()
        existing = conn.execute(
            "SELECT id FROM subscriptions WHERE merchant_key = ? AND owner_id IS NULL",
            (merchant_key,),
        ).fetchone()
        if existing:
            sub_id = existing["id"]
        else:
            sub_id = db.upsert_subscription(
                conn, None,  # service-created: unclaimed until the user interacts
                merchant=merchant, merchant_key=merchant_key,
                amount=amount, currency=currency, frequency=frequency,
                confidence=0.7, first_seen="", last_seen="",
                occurrences=max(occurrences, 1),
            )
        conn.close()

    sub = {"merchant": merchant, "amount": amount, "currency": currency,
           "frequency": frequency, "occurrences": occurrences}
    nudge = nudge_for(sub) if amount > 0 else (
        f"Heads up — I spotted repeat charges from {merchant}. "
        "Want me to walk you through cancelling? One tap."
    )
    return {
        "event_type": req.event_type,
        "handled": True,
        "subscription_id": sub_id,
        "user_message": nudge,
    }
