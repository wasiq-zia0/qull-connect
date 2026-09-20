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
from typing import Literal
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ConfigDict, BaseModel, Field, field_validator

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import billing
import db
import scanner
from identity import IdentityMiddleware, IdentityError, require_owner
from limits import RateLimitMiddleware, BodySizeLimitMiddleware

# Shared life-event bus (suite compounding: one intelligence, ten connectors).

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
app.add_middleware(IdentityMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(BodySizeLimitMiddleware)


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
    guide_key = sub["merchant_key"]
    for suffix in ("_usd", "_eur", "_gbp", "_cad"):
        guide_key = guide_key.removesuffix(suffix)
    guide = guides.get("merchants", {}).get(guide_key)
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

class SafeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class ScanRequest(SafeModel):
    source: str = Field(default="gmail", description="'gmail' or 'fixtures'")
    max: int = Field(default=100, ge=1, le=500)

    @field_validator("source")
    @classmethod
    def _source_ok(cls, v: str) -> str:
        if v not in ("gmail", "fixtures"):
            raise ValueError("source must be 'gmail' or 'fixtures'")
        return v


class SubscriptionUpdate(SafeModel):
    status: str | None = None
    savings_monthly: float | None = Field(default=None, ge=0, le=100000)
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("status")
    @classmethod
    def _status_ok(cls, v: str | None) -> str | None:
        if v is not None and v not in STATUSES:
            raise ValueError(f"status must be one of {sorted(STATUSES)}")
        return v


class BillingSetupRequest(SafeModel):
    accept_fee_terms: Literal[True]
    name: str = Field(..., min_length=1, max_length=100)
    email: str = Field(default="", max_length=200)


class SavingsConfirmRequest(SafeModel):
    fee_amount_cents: int = Field(..., gt=0)
    confirm_fee: Literal[True]
    subscription_ids: list[int] = Field(..., min_length=1, max_length=100)


class LifeEventRequest(SafeModel):
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


class Receipt(SafeModel):
    subject: str = Field(default="", max_length=500)
    sender: str = Field(default="", max_length=500)
    snippet: str = Field(default="", max_length=2000)
    body: str = Field(default="", max_length=8000)
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")

    @field_validator("date")
    @classmethod
    def valid_date(cls, value: str) -> str:
        from datetime import date
        date.fromisoformat(value)
        return value


class ReceiptImport(SafeModel):
    receipts: list[Receipt] = Field(..., min_length=1, max_length=100)


class ManualSubscription(SafeModel):
    merchant: str = Field(..., min_length=1, max_length=120)
    amount: float = Field(..., gt=0, le=100000)
    currency: Literal["USD", "EUR", "GBP", "CAD"] = "USD"
    frequency: Literal["monthly", "yearly"] = "monthly"


def _detected_response(detected: list[dict], *, persist: bool, owner: str) -> dict:
    conn = get_conn() if persist else None
    totals: dict[str, float] = {}
    try:
        for sub in detected:
            if not sub["recurring"] or sub["amount"] is None:
                continue
            if conn is not None:
                sub["subscription_id"] = db.upsert_subscription(conn, owner, **{k: sub[k] for k in (
                    "merchant", "merchant_key", "amount", "currency", "frequency", "confidence", "first_seen", "last_seen", "occurrences")})
            annual = sub["amount"] * (12 if sub["frequency"] == "monthly" else 1)
            totals[sub["currency"]] = round(totals.get(sub["currency"], 0) + annual, 2)
    finally:
        if conn is not None:
            conn.close()
    return {"detected": detected, "demo": not persist,
            "recurring_count": sum(s["recurring"] for s in detected),
            "estimated_annual_cost_by_currency": totals,
            "user_message": ("Demo receipts only; no subscriptions were saved or charged." if not persist else
                             "Possible recurring charges were added to your tracker. Review each result before using its cancellation guide.")}


@app.post("/api/scan")
def scan(req: ScanRequest):
    """Fixture-only demonstration. Gmail integration requires per-user OAuth and is unavailable."""
    owner = require_owner()
    if req.source != "fixtures":
        raise HTTPException(501, "Gmail access is not connected. Use POST /api/receipts or POST /api/subscriptions with information you provide.")
    result = scanner.scan_and_detect(source="fixtures", max_results=req.max)
    return _detected_response(result["subscriptions"], persist=False, owner=owner)


@app.post("/api/receipts", status_code=201)
def import_receipts(req: ReceiptImport):
    """Detect recurring charges from user-provided receipt text. Raw receipts are not stored."""
    owner = require_owner()
    from detector import detect_recurring
    return _detected_response(detect_recurring([r.model_dump() for r in req.receipts]), persist=True, owner=owner)


@app.post("/api/subscriptions", status_code=201)
def add_subscription(req: ManualSubscription):
    """Track a recurring charge supplied by the user; does not contact the merchant."""
    owner = require_owner()
    import hashlib
    from datetime import date
    from detector import normalise_merchant
    _, known_key = normalise_merchant("", req.merchant)
    key = known_key + "_" + req.currency.lower() if known_key != "merchant_unknown" else "manual_" + hashlib.sha256((req.merchant.casefold() + req.currency).encode()).hexdigest()[:24]
    conn = get_conn()
    try:
        sid = db.upsert_subscription(conn, owner, merchant=clean(req.merchant, 120), merchant_key=key,
                                    amount=req.amount, currency=req.currency, frequency=req.frequency,
                                    confidence=1, first_seen=date.today().isoformat(), last_seen=date.today().isoformat(), occurrences=1)
        sub = row_to_dict(get_sub_or_404(conn, sid, owner))
    finally:
        conn.close()
    return {"subscription": sub, "user_message": "Subscription tracked. Use its cancellation guide and confirm with the merchant before recording a cancellation."}


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
    totals = {}
    for sub in subs:
        if sub["status"] != "cancelled":
            totals[sub["currency"]] = round(totals.get(sub["currency"], 0) + sub["amount"] * (12 if sub["frequency"] == "monthly" else 1), 2)
    if not subs:
        msg = "No subscriptions tracked yet. Run a scan and I'll list everything I find."
    else:
        msg = f"You are tracking {len(subs)} subscriptions. Review the list and choose a cancellation guide."
    return {"subscriptions": subs, "count": len(subs), "estimated_annual_cost_by_currency": totals, "user_message": msg}


@app.patch("/api/subscriptions/{sub_id}")
def update_subscription(sub_id: int, req: SubscriptionUpdate):
    owner = require_owner()
    conn = get_conn()
    row = get_sub_or_404(conn, sub_id, owner)
    sub = row_to_dict(row)
    if sub.get("confirmation_batch"):
        conn.close()
        raise HTTPException(409, "Cancellation confirmation is already in progress or complete; its fee basis is locked.")
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
    existing_billing = _billing_row(conn, owner)
    name = clean(req.name, 100)
    email = clean(req.email, 200)
    customer = ({"customer_id": existing_billing["customer_id"]} if existing_billing else
                billing.setup_customer(name, email=email, idempotency_key=f"{billing.CONNECTOR}-customer-{owner}"))
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
        "INSERT OR REPLACE INTO billing (owner_id, customer_id, setup_intent_id, checkout_session_id, created_at) VALUES (?,?,?,?,?)",
        (owner, customer["customer_id"], setup.get("setup_intent_id"), setup.get("checkout_session_id"), db._utcnow()),
    )
    conn.commit()
    conn.close()
    return {
        "customer_id": customer["customer_id"],
        "client_secret": setup.get("client_secret"),
        "setup_url": setup.get("setup_url"),
        "checkout_session_id": setup.get("checkout_session_id"),
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
    verified = billing.retrieve_card_setup(row["customer_id"], setup_intent_id=row["setup_intent_id"], checkout_session_id=row["checkout_session_id"]) if row else {}
    billing_status = "card_ready" if verified.get("status") == "succeeded" else ("card_pending" if row else "none")
    card_state = "ready" if billing_status == "card_ready" else _card_state(row)
    state_msg = {
        "ready": "Your payment method is saved; fees require your explicit confirmation.",
        "none": "No card on file yet — set up billing when you're ready.",
        "pending": "Card setup has started. Complete the secure setup link before confirming a fee.",
    }.get(card_state, "Billing state unknown — ask me to check.")
    return {"billing_status": billing_status,
            "card_state": card_state,
            "fee_disclosure": billing.FEE_DISCLOSURE,
            "user_message": state_msg}


class SavingsQuoteRequest(SafeModel):
    subscription_ids: list[int] = Field(..., min_length=1, max_length=100)


@app.post("/api/savings/fee-quote")
def savings_fee_quote(req: SavingsQuoteRequest) -> dict:
    """Read-only quote for the exact completed cancellation IDs. Does not reserve or charge."""
    owner = require_owner()
    if len(set(req.subscription_ids)) != len(req.subscription_ids):
        raise HTTPException(422, "subscription_ids must not contain duplicates")
    conn = get_conn()
    try:
        subs = [row_to_dict(get_sub_or_404(conn, sid, owner)) for sid in req.subscription_ids]
    finally:
        conn.close()
    if any(s["status"] != "cancelled" or (s.get("savings_monthly") or 0) <= 0 for s in subs):
        raise HTTPException(409, "Complete the cancellations and record monthly savings before requesting a fee quote.")
    if any(s["savings_confirmed"] for s in subs):
        raise HTTPException(409, "A cancellation in this set is already paid.")
    cents = billing.fee_cents(len(subs))
    return {"subscription_ids": sorted(req.subscription_ids), "fee_amount_cents": cents,
            "currency": "usd", "fee_disclosure": billing.FEE_DISCLOSURE,
            "requires_confirmation": True,
            "user_message": f"The fee for these {len(subs)} completed cancellation(s) is ${cents / 100:.2f} USD. Confirm this exact amount only if you agree to pay it."}


@app.post("/api/savings/confirmed")
def confirm_savings(req: SavingsConfirmRequest):
    """User-confirmed cancellations trigger the $10-per-cancellation off-session fee charge."""
    owner = require_owner()
    if len(set(req.subscription_ids)) != len(req.subscription_ids):
        raise HTTPException(422, "subscription_ids must not contain duplicates")
    conn = get_conn()
    record_id = "subs-" + "-".join(str(sid) for sid in sorted(req.subscription_ids))
    conn.execute("BEGIN IMMEDIATE")
    rows = []
    try:
        for sid in req.subscription_ids:
            rows.append(get_sub_or_404(conn, sid, owner))
    except HTTPException:
        conn.close()
        raise
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
    conflicting = [s["id"] for s in subs if s.get("confirmation_batch") not in (None, record_id)]
    if conflicting:
        conn.close()
        raise HTTPException(409, "A cancellation is already reserved by another confirmation. Retry its original set of IDs.")
    fee = billing.fee_cents(len(subs))
    if req.fee_amount_cents != fee:
        conn.close()
        raise HTTPException(409, detail={"error": "fee_quote_changed", "fee_amount_cents": fee})
    for sub in subs:
        conn.execute("UPDATE subscriptions SET confirmation_batch=? WHERE id=? AND owner_id=?", (record_id, sub["id"], owner))
    conn.commit()

    total = billing.first_year_savings([s for s in subs if s["currency"] == "USD"])  # informational only; the fee is flat
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
        idempotency_key=f"{billing.CONNECTOR}-fee-{owner}-{record_id}",
        setup_intent_id=billing_row["setup_intent_id"], checkout_session_id=billing_row["checkout_session_id"], consent=req.confirm_fee)
    if "error" in charge:
        conn.execute(
            "INSERT INTO charges (owner_id, customer_id, amount_cents, currency, description, subscription_ids, status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (owner, customer_id, fee, "usd", clean(desc, 200),
             json.dumps(req.subscription_ids), "failed", db._utcnow()),
        )
        conn.commit()
        conn.close()
        return _payment_error(charge)

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
        "first_year_savings_usd": total,
        "cancellations_confirmed": n,
        "fee_per_cancellation_usd": billing.FEE_PER_CANCELLATION_CENTS / 100,
        "fee_charged": round(fee / 100, 2),
        "payment_intent_id": charge.get("payment_intent_id"),
        "subscription_ids": req.subscription_ids,
        "user_message": (f"You confirmed {n} completed cancellation(s). The one-time USD fee of {money(fee / 100)} is paid. "
                         "Keep the merchant's cancellation confirmation and check your next statement."),
    }


@app.post("/api/life-events")
def receive_life_event(req: LifeEventRequest):
    """Receive user-authorized charge metadata; never a shared service-key draft."""
    owner = require_owner()
    if req.event_type != "recurring_charge_detected":
        return {
            "event_type": req.event_type,
            "handled": False,
            "user_message": "Got it — that event isn't one I act on, so I'll stay quiet.",
        }
    payload = req.payload or {}
    try:
        validated = ManualSubscription(merchant=payload.get("merchant", ""), amount=payload.get("amount"),
                                       currency=payload.get("currency", "USD"), frequency=payload.get("frequency", "monthly"))
    except ValueError:
        raise HTTPException(422, "Provide merchant, positive amount, supported currency, and monthly/yearly frequency.")
    return {"handled": True, "event_type": req.event_type, **add_subscription(validated)}


@app.delete("/api/data")
def delete_my_data() -> dict:
    """Delete the authenticated user's operational data. Payment/accounting records remain with the processor and protected billing ledger."""
    owner = require_owner()
    conn = get_conn()
    try:
        conn.execute("DELETE FROM subscriptions WHERE owner_id=?", (owner,))
        conn.execute("DELETE FROM billing WHERE owner_id=?", (owner,))
        # Retain required charge amounts/processor references; strip names and subscription metadata.
        conn.execute("UPDATE charges SET description='Retained payment record', subscription_ids='[]' WHERE owner_id=?", (owner,))
        conn.commit()
    finally:
        conn.close()
    return {"deleted": True, "retained": "Required payment/accounting records and processor records; backups follow the operator's retention policy.", "user_message": "Your operational records for this connector were deleted. Required payment records are retained separately."}



def _payment_error(result: dict) -> JSONResponse:
    code = result.get("code")
    status = {"payment_processing": 202, "billing_conflict": 409, "payment_not_captured": 409,
              "billing_configuration": 503, "billing_unavailable": 503, "billing_busy": 503,
              "billing_failed": 503, "invalid_fee": 422, "consent_required": 422}.get(code, 402)
    return JSONResponse(status_code=status, content=result)


from api_support import install_api_contract
install_api_contract(app, "subscription-slayer", app.title)
