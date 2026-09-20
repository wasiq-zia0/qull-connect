"""Moving Concierge — address-change automation pack for movers. Flat $49/move.

Agent-native design: EVERY endpoint response includes a `user_message` field —
a warm, ready-to-speak sentence the agent can say verbatim in chat. There is no
app screen; the agent is the UI.

Golden path: trigger (life event or "I moved") -> one tap "yes" -> save card ->
pay $49 -> pack delivered. The pack itself is the product; we never file a
change-of-address anywhere — the user completes each step via the official links.
"""
import os
import uuid
from datetime import date, datetime, timezone
from typing import Literal
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import ConfigDict, BaseModel, Field, field_validator

from src import db, billing
from src.identity import IdentityMiddleware, IdentityError, require_owner
from src.limits import BodySizeLimitMiddleware, RateLimitMiddleware
from src.states import get_state
from src.pack import build_checklist, render_markdown, build_pdf, sanitize
from src.billing import (setup_customer, create_card_setup, charge_pack,
                         FEE_LABEL, FEE_DISCLOSURE, CONNECTOR)

_PROD = os.environ.get("ENV") == "production"
_PUBLIC_DOCS = os.environ.get("PUBLIC_DOCS") == "1"
app = FastAPI(title="Moving Concierge API", version="0.1.0",
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
                        content={"error": msg, "user_message": msg})
PACKS_DIR = Path(os.environ.get("DATA_DIR", str(Path(__file__).resolve().parent / "data"))) / "packs_out"
PACKS_DIR.mkdir(parents=True, exist_ok=True)

VALID_STATUSES = {"pending", "done", "na"}


# ---------- models ----------

class SafeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class BillingConsent(SafeModel):
    accept_fee_terms: Literal[True]


class ChargeConsent(SafeModel):
    fee_amount_cents: int = Field(..., gt=0)
    confirm_fee: Literal[True] = Field(..., description="Explicit permission to charge the disclosed fee for this record.")


class MoveIn(SafeModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: str = Field(..., min_length=3, max_length=200)
    old_address: str = Field(..., min_length=1, max_length=500)
    new_address: str = Field(..., min_length=1, max_length=500)
    move_date: date
    state: str = Field(..., min_length=2, max_length=2,
                       description="2-letter US state / DC code for the NEW address (DMV + voter rules)")
    draft_id: str | None = Field(None, description="Draft move id from POST /api/life-events to upgrade in place")

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("must be a valid email address")
        return v.strip()

    @field_validator("state")
    @classmethod
    def _state(cls, v: str) -> str:
        return v.strip().upper()


class LifeEventIn(SafeModel):
    event_type: str
    payload: dict = Field(default_factory=dict)


class ChecklistItemUpdate(SafeModel):
    item_id: int
    status: str = Field(..., description="pending | done | na")


class ChecklistPatch(SafeModel):
    items: list[ChecklistItemUpdate] = Field(..., min_length=1)


# ---------- helpers ----------

@app.exception_handler(RequestValidationError)
async def validation_handler(_, exc: RequestValidationError):
    msgs = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
    return JSONResponse(status_code=422, content={
        "error": f"Invalid request: {msgs}",
        "user_message": "Hmm, something in that request didn't look right — mind double-checking the details and trying again?",
    })


_HTTP_USER_MESSAGES = {
    400: "Hmm, that didn't quite work — mind checking the details and trying again?",
    402: "Your pack just needs the $49 unlock — say the word and I'll charge it.",
    404: "I couldn't find that one — want to double-check and try again?",
    500: "Something went wrong on my end — give me a moment, then let's try again.",
    502: "Payment status could not be confirmed. Check billing status before retrying.",
}


@app.exception_handler(HTTPException)
async def http_error_handler(_, exc: HTTPException):
    detail = exc.detail
    return JSONResponse(status_code=exc.status_code, content={
        "error": detail,
        "user_message": _HTTP_USER_MESSAGES.get(
            exc.status_code, "Something didn't work out — let's try again."),
    })


def _get_move_or_404(move_id: str, owner: str) -> dict:
    """Owner-scoped read. Legacy ownerless drafts remain inaccessible."""
    conn = db.connect()
    row = conn.execute("SELECT * FROM moves WHERE id = ? AND owner_id = ?",
                       (move_id, owner)).fetchone()
    if row is None:
        conn.close()
        raise HTTPException(404, "Move not found")
    conn.close()
    return dict(row)


def _items_for(move_id: str, owner: str) -> list[dict]:
    conn = db.connect()
    rows = conn.execute(
        "SELECT * FROM checklist_items WHERE move_id = ? AND owner_id = ? ORDER BY sort, id",
        (move_id, owner)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _create_items(move_id: str, move: dict, state: dict, owner_id: str) -> list[dict]:
    conn = db.connect()
    try:
        items = []
        for i, tpl in enumerate(build_checklist(move, state)):
            cur = conn.execute(
                "INSERT INTO checklist_items (move_id, category, title, detail, url, status, sort, owner_id)"
                " VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
                (move_id, tpl["category"], tpl["title"], tpl["detail"], tpl["url"], i, owner_id))
            items.append({"item_id": cur.lastrowid, **tpl, "status": "pending"})
        conn.commit()
        return items
    finally:
        conn.close()


def _progress(items: list[dict]) -> dict:
    total = len(items)
    done = sum(1 for i in items if i["status"] in ("done", "na"))
    return {"total": total, "done": done,
            "remaining": total - done,
            "percent": round(done / total * 100) if total else 0}


def _short(addr: str, n: int = 48) -> str:
    s = sanitize(addr, n)
    return s if len(sanitize(addr)) <= n else s + "…"


# ---------- endpoints ----------

@app.get("/health")
def health():
    return {"ok": True, "service": "moving-concierge",
            "user_message": "Moving Concierge is up and ready to pack."}


@app.post("/api/life-events")
def life_event(evt: LifeEventIn):
    """Receive a fanned-out life event. A 'move' creates a draft move + the proactive nudge."""
    owner = require_owner()
    if evt.event_type != "move":
        return {"acknowledged": True, "acted": False, "event_type": evt.event_type,
                "user_message": "Noted — nothing for me to pack there."}
    p = evt.payload or {}
    move_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    state_abbr = str(p.get("state") or p.get("to_state") or "").strip().upper()
    state = None
    if state_abbr:
        try:
            state = get_state(state_abbr)
        except KeyError:
            state_abbr = ""
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO moves (id, name, email, old_address, new_address, move_date, state, status, created_at, owner_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)",
            (move_id,
             sanitize(p.get("name") or "there", 200),
             sanitize(p.get("email") or "", 200),
             sanitize(p.get("from_address") or p.get("old_address") or "", 500),
             sanitize(p.get("to_address") or p.get("new_address") or "", 500),
             sanitize(p.get("move_date") or "", 20),
             state_abbr, now, owner))
        conn.commit()
    finally:
        conn.close()

    old_s = _short(p.get("from_address") or p.get("old_address") or "your old place")
    new_s = _short(p.get("to_address") or p.get("new_address") or "your new place")
    if state:
        nudge = (f"Looks like you moved from {old_s} to {new_s}. I can prepare a checklist for the "
                 f"address-change circus — USPS, banks, voter registration, your {state['state']} "
                 f"driver's license (you've got {state['dmv_deadline']}) — in one $49 pack. "
                 f"Want it? One tap.")
    else:
        nudge = (f"Looks like you moved from {old_s} to {new_s}. I can prepare a checklist for the "
                 f"address-change circus — USPS, banks, voter registration, your driver's "
                 f"license — in one $49 pack. Want it? One tap.")
    return {"acknowledged": True, "acted": True, "move_id": move_id, "status": "draft",
            "user_message": nudge}


@app.post("/api/moves")
def create_move(payload: MoveIn):
    """Intake: build the move + checklist, emit the move event to the shared bus."""
    owner = require_owner()
    try:
        state = get_state(payload.state)
    except KeyError:
        raise HTTPException(400, f"Unknown state abbreviation {payload.state!r}. "
                                 "Use a 2-letter US state or DC code for the new address.")

    move_id = payload.draft_id or uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    move = {"id": move_id, "name": payload.name.strip(), "email": payload.email,
            "old_address": payload.old_address.strip(), "new_address": payload.new_address.strip(),
            "move_date": payload.move_date.isoformat(), "state": payload.state}

    conn = db.connect()
    try:
        if payload.draft_id:
            existing = conn.execute("SELECT id, owner_id, status FROM moves WHERE id = ?",
                                    (move_id,)).fetchone()
            if not existing:
                raise HTTPException(404, f"No draft move found with id {payload.draft_id!r}")
            if existing["owner_id"] != owner:
                raise HTTPException(404, f"No draft move found with id {payload.draft_id!r}")
            if existing["status"] != "draft":
                raise HTTPException(409, "Only a draft move can be completed; create a new move for different addresses.")
            conn.execute(
                "UPDATE moves SET name=?, email=?, old_address=?, new_address=?, move_date=?, state=?, status='ready', owner_id=?"
                " WHERE id = ?",
                (move["name"], move["email"], move["old_address"], move["new_address"],
                 move["move_date"], move["state"], owner, move_id))
            conn.execute("DELETE FROM checklist_items WHERE move_id = ?", (move_id,))
        else:
            conn.execute(
                "INSERT INTO moves (id, name, email, old_address, new_address, move_date, state, status, created_at, owner_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?)",
                (move_id, move["name"], move["email"], move["old_address"], move["new_address"],
                 move["move_date"], move["state"], now, owner))
        conn.commit()
    finally:
        conn.close()

    items = _create_items(move_id, move, state, owner)
    fanout = {"fanned_out_to": []}

    first = sanitize(move["name"]).split()[0] if sanitize(move["name"]) else "there"
    return {
        "move_id": move_id, "status": "ready",
        "state": state["state"], "dmv_deadline": state["dmv_deadline"],
        "checklist": _progress(items),
        "fanned_out_to": fanout.get("fanned_out_to", []),
        "user_message": (
            f"Got it, {first} — your address-change pack for the move to {_short(move['new_address'])} "
            f"is built: {len(items)} stops including USPS mail forwarding, your {state['state']} "
            f"driver's license (due {state['dmv_deadline']}), and voter registration. "
            f"It's a flat $49 for the whole pack, charged once after you confirm. "
            f"Want me to unlock it? One tap."),
    }


@app.get("/api/moves/{move_id}")
def get_move(move_id: str):
    owner = require_owner()
    move = _get_move_or_404(move_id, owner)
    items = _items_for(move_id, owner)
    prog = _progress(items)
    nxt = next((i for i in items if i["status"] == "pending"), None)
    first = sanitize(move["name"]).split()[0] if sanitize(move["name"]) else "there"
    if prog["remaining"] == 0 and prog["total"]:
        msg = (f"That's everything, {first} — all {prog['total']} stops completed or marked not applicable. "
               f"You're officially moved. 🎉")
    elif nxt:
        msg = (f"Your move to {_short(move['new_address'])}: {prog['done']} of {prog['total']} "
               f"stops done. Next up: {sanitize(nxt['title'])}.")
    else:
        msg = (f"Your move to {_short(move['new_address'])} is ready — "
               f"{prog['total']} stops in your pack.")
    return {"move_id": move_id, **{k: move[k] for k in
            ("name", "email", "old_address", "new_address", "move_date", "state",
             "status", "billing_status", "paid_at")},
            "checklist": prog, "items": items if move["status"] == "paid" else [],
            "pack_locked": move["status"] != "paid", "user_message": msg}


@app.get("/api/moves/{move_id}/pack")
def get_pack(move_id: str, format: str = Query("md", pattern="^(md|pdf)$")):
    owner = require_owner()
    move = _get_move_or_404(move_id, owner)
    if move["status"] != "paid":
        raise HTTPException(402, f"The pack unlocks with the one-time {FEE_LABEL} charge. "
                                 f"POST /api/moves/{move_id}/billing/setup to save a card, "
                                 f"then POST /api/moves/{move_id}/pay.")
    try:
        state = get_state(move["state"])
    except KeyError:
        raise HTTPException(500, "Move has an unknown state; cannot build pack")
    items = _items_for(move_id, owner)
    if format == "pdf":
        path = str(PACKS_DIR / f"pack-{move_id}.pdf")
        build_pdf(move, state, items, path)
        return FileResponse(path, media_type="application/pdf",
                            filename=f"moving-concierge-pack-{move_id}.pdf")
    markdown = render_markdown(move, state, items)
    prog = _progress(items)
    return {"move_id": move_id, "format": "markdown", "markdown": markdown,
            "checklist": prog,
            "pdf_url": f"/api/moves/{move_id}/pack?format=pdf",
            "user_message": (
                f"Here's your pack, {sanitize(move['name']).split()[0] if sanitize(move['name']) else 'friend'} — "
                f"{prog['total']} stops, starting with USPS mail forwarding and your "
                f"{state['state']} driver's license, due {state['dmv_deadline']}. "
                f"There's a printable PDF too. You've got this. 💪")}


@app.patch("/api/moves/{move_id}/checklist")
def update_checklist(move_id: str, payload: ChecklistPatch):
    owner = require_owner()
    move = _get_move_or_404(move_id, owner)
    if move["status"] != "paid":
        raise HTTPException(402, "Unlock the pack before updating its checklist.")
    items = _items_for(move_id, owner)
    by_id = {i["id"]: i for i in items}
    for u in payload.items:
        if u.status not in VALID_STATUSES:
            raise HTTPException(400, f"Invalid status {u.status!r} for item {u.item_id}. "
                                     "Use pending, done, or na.")
        if u.item_id not in by_id:
            raise HTTPException(404, f"No checklist item {u.item_id} on move {move_id}")
    conn = db.connect()
    try:
        for u in payload.items:
            conn.execute("UPDATE checklist_items SET status = ? WHERE id = ? AND move_id = ? AND owner_id = ?",
                         (u.status, u.item_id, move_id, owner))
        conn.commit()
    finally:
        conn.close()
    items = _items_for(move_id, owner)
    prog = _progress(items)
    if prog["remaining"] == 0:
        msg = (f"That's everything — all {prog['total']} stops completed or marked not applicable. You're officially moved. 🎉")
    else:
        nxt = next((i for i in items if i["status"] == "pending"), None)
        nxt_s = f" Next up: {sanitize(nxt['title'])}." if nxt else ""
        msg = (f"Nice — {prog['done']} of {prog['total']} stops done, {prog['remaining']} to go.{nxt_s}")
    return {"move_id": move_id, "checklist": prog, "items": items, "user_message": msg}


@app.post("/api/moves/{move_id}/billing/setup")
def billing_setup(move_id: str, consent: BillingConsent):
    """Create the Stripe customer + card SetupIntent. Honest fee disclosure BEFORE any charge."""
    owner = require_owner()
    move = _get_move_or_404(move_id, owner)
    if move["status"] == "paid":
        raise HTTPException(400, "This move's pack fee is already paid.")
    if move["status"] == "draft":
        raise HTTPException(409, "Complete the move details before setting up billing.")
    cust = ({"customer_id": move["stripe_customer_id"]} if move.get("stripe_customer_id") else
            setup_customer(move["name"], move["email"], idempotency_key=f"{CONNECTOR}-customer-{owner}-{move_id}"))
    if "error" in cust:
        raise HTTPException(502, f"Stripe customer creation failed: {cust['error']}")
    setup = create_card_setup(
        cust["customer_id"],
        idempotency_key=f"{CONNECTOR}-setup-{owner}-{move_id}")
    if "error" in setup:
        raise HTTPException(502, f"Stripe card setup failed: {setup['error']}")
    conn = db.connect()
    try:
        conn.execute("UPDATE moves SET stripe_customer_id = ?, stripe_setup_intent_id = ?, checkout_session_id = ?, billing_status = 'card_pending' WHERE id = ? AND owner_id = ?",
                     (cust["customer_id"], setup.get("setup_intent_id"), setup.get("checkout_session_id"), move_id, owner))
        conn.commit()
    finally:
        conn.close()
    return {"move_id": move_id, "stripe_customer_id": cust["customer_id"],
            "client_secret": setup.get("client_secret"), "setup_url": setup.get("setup_url"),
            "checkout_session_id": setup.get("checkout_session_id"),
            "fee": FEE_LABEL, "fee_amount_cents": 4900, "currency": "usd", "fee_disclosure": FEE_DISCLOSURE,
            "user_message": (
                f"Your pack is ready. {FEE_DISCLOSURE} "
                f"Save your card now — nothing is charged until you say the word.")}


@app.get("/api/moves/{move_id}/billing/status")
def billing_status(move_id: str):
    """Pollable billing state: card state + whether the $49 pack fee is settled."""
    owner = require_owner()
    move = _get_move_or_404(move_id, owner)
    bs = move["billing_status"] or "none"
    verified = billing.retrieve_card_setup(move["stripe_customer_id"], setup_intent_id=move.get("stripe_setup_intent_id"), checkout_session_id=move.get("checkout_session_id")) if move.get("stripe_customer_id") else {}
    if bs == "card_pending" and verified.get("status") == "succeeded":
        bs = "card_ready"
    card_state = {"card_ready": "ready", "none": "none", "card_pending": "pending", "paid": "ready"}.get(bs, bs)
    um = {"none": "No card on file yet — say the word and I'll set one up.",
          "card_pending": "Complete the secure card setup link. No fee has been confirmed.",
          "card_ready": "Your payment method is ready; the $49 fee requires your confirmation.",
          "paid": "The $49 pack fee is paid — your pack is unlocked."}.get(
              bs, "Billing state unclear — ask me and I'll check.")
    return {"move_id": move_id,
            "billing_status": bs,
            "card_state": card_state,
            "fee": FEE_LABEL, "fee_amount_cents": 4900, "currency": "usd",
            "fee_disclosure": FEE_DISCLOSURE,
            "user_message": um}


@app.post("/api/moves/{move_id}/pay")
def pay(move_id: str, consent: ChargeConsent):
    """Charge the flat $49.00 pack fee off-session against the saved card, then deliver the pack."""
    owner = require_owner()
    move = _get_move_or_404(move_id, owner)
    if move["status"] == "paid":
        raise HTTPException(400, "This move's pack fee is already paid — the pack is unlocked.")
    if not move.get("stripe_customer_id"):
        raise HTTPException(400, f"No card on file. {FEE_DISCLOSURE} "
                                  f"POST /api/moves/{move_id}/billing/setup first to save a card.")
    if consent.fee_amount_cents != 4900:
        raise HTTPException(409, detail={"error": "fee_quote_changed", "fee_amount_cents": 4900})
    # The fee amount is the server-side FEE_CENTS constant — never client input.
    # The stored customer id is used; nothing from the request reaches Stripe.
    result = charge_pack(
        move["stripe_customer_id"],
        idempotency_key=f"{CONNECTOR}-fee-{owner}-{move_id}",
        setup_intent_id=move.get("stripe_setup_intent_id"), checkout_session_id=move.get("checkout_session_id"), consent=consent.confirm_fee)
    if "error" in result:
        return _payment_error(result)
    now = datetime.now(timezone.utc).isoformat()
    conn = db.connect()
    try:
        # Atomic compare-and-set: a concurrent retry can never double-mark paid.
        cur = conn.execute("UPDATE moves SET status = 'paid', billing_status = 'paid', paid_at = ?, stripe_payment_intent_id = ?"
                           " WHERE id = ? AND owner_id = ? AND status != 'paid'",
                           (now, result["payment_intent_id"], move_id, owner))
        conn.commit()
    finally:
        conn.close()
    if cur.rowcount == 0:
        raise HTTPException(400, "This move's pack fee is already paid — the pack is unlocked.")
    move = _get_move_or_404(move_id, owner)
    state = get_state(move["state"])
    items = _items_for(move_id, owner)
    markdown = render_markdown(move, state, items)
    first = sanitize(move["name"]).split()[0] if sanitize(move["name"]) else "friend"
    return {"move_id": move_id, "status": "paid",
            "charged": result["amount_label"], "payment_intent_id": result["payment_intent_id"],
            "fee_disclosure": FEE_DISCLOSURE,
            "pack_markdown": markdown,
            "pdf_url": f"/api/moves/{move_id}/pack?format=pdf",
            "checklist": _progress(items),
            "user_message": (
                f"Done, {first} — {result['amount_label']} charged, once. Your pack is live: "
                f"{len(items)} stops, starting with USPS mail forwarding and your "
                f"{state['state']} driver's license, due {state['dmv_deadline']}. "
                "Use the checklist to complete each address change yourself; no forms have been filed for you.")}


@app.delete("/api/data")
def delete_my_data() -> dict:
    """Delete the authenticated user's operational data. Payment/accounting records remain with the processor and protected billing ledger."""
    owner = require_owner()
    conn = db.connect()
    try:
        ids = [row["id"] for row in conn.execute("SELECT id FROM moves WHERE owner_id=?", (owner,))]
        conn.execute("DELETE FROM checklist_items WHERE owner_id=?", (owner,))
        conn.execute("DELETE FROM moves WHERE owner_id=?", (owner,))
        conn.commit()
    finally:
        conn.close()
    for move_id in ids:
        (PACKS_DIR / f"pack-{move_id}.pdf").unlink(missing_ok=True)
    return {"deleted": True, "retained": "Required payment/accounting records and processor records; backups follow the operator's retention policy.", "user_message": "Your operational records for this connector were deleted. Required payment records are retained separately."}



def _payment_error(result: dict) -> JSONResponse:
    code = result.get("code")
    status = {"payment_processing": 202, "billing_conflict": 409, "payment_not_captured": 409,
              "billing_configuration": 503, "billing_unavailable": 503, "billing_busy": 503,
              "billing_failed": 503, "invalid_fee": 422, "consent_required": 422}.get(code, 402)
    return JSONResponse(status_code=status, content=result)


from api_support import install_api_contract
install_api_contract(app, "moving-concierge", app.title)
