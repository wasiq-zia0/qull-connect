"""Shared business logic for the class-action-cash connector.

Used by both the FastAPI REST API (app.py) and the MCP server
(mcp_server.py), so the two interfaces behave identically.

Design rule: the golden path is trigger -> one tap -> done.
Everything heavier is an advanced path.
"""
import json
import os
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

DIR = Path(__file__).resolve().parent
DATA = DIR / "data"
SETTLEMENTS_FILE = DATA / "open_settlements.json"
FIXTURES_FILE = DATA / "fixtures" / "gmail_receipts.json"

# Import the shared life-events bus (no server, no deps).
sys.path.insert(0, str(Path.home() / "workspace/connectors/life-events"))
import events as life_events  # noqa: E402

import db  # noqa: E402
import matcher  # noqa: E402
import billing  # noqa: E402
from safety import sanitize  # noqa: E402

import re  # noqa: E402

FEE_RATE = 0.20
FRESHNESS_STALE_DAYS = 90

FEE_DISCLOSURE = (
    "You will be charged 20% of the confirmed settlement payout, "
    "only if you confirm the payout. No charge otherwise."
)


def _owned_match(match_id: str, owner: str) -> dict:
    """Fetch a match belonging to owner. An ownerless life-event draft is
    claimed by the first owner who presents its unguessable ID; anyone
    else's match raises KeyError (surfaced as 404)."""
    match = db.get_match(match_id, owner)
    if match:
        return match
    claimed = db.claim_match(match_id, owner)
    if claimed:
        return claimed
    raise KeyError(f"No match found with id {match_id}.")


def _expected_payout_ceiling(match: dict) -> float | None:
    """Best-effort ceiling of the settlement's published payout range, parsed
    from the match's typical_payout_range text. None when unparseable."""
    text = match.get("typical_payout_range") or ""
    vals = []
    for m in re.finditer(r"\$\s*([\d,]+(?:\.\d+)?)\s*([KkMmBb]?)", text):
        num = float(m.group(1).replace(",", ""))
        mult = {"k": 1e3, "m": 1e6, "b": 1e9}.get(m.group(2).lower(), 1)
        vals.append(num * mult)
    return max(vals) if vals else None


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_settlements() -> list:
    return json.loads(SETTLEMENTS_FILE.read_text())


def settlement_freshness() -> dict:
    settlements = load_settlements()
    dates = [s["last_verified"] for s in settlements if s.get("last_verified")]
    if not dates:
        return {"stale": True, "days_since_verified": None,
                "warning": "No verification dates on file — treat every settlement as unverified until refreshed."}
    newest = max(dates)
    days = (date.today() - date.fromisoformat(newest)).days
    stale = days > FRESHNESS_STALE_DAYS
    warning = None
    if stale:
        warning = (
            f"Settlement data is {days} days old (last verified {newest}). "
            "Deadlines may have changed or closed — confirm each claim deadline "
            "on the settlement's official site before filing."
        )
    return {"stale": stale, "days_since_verified": days,
            "last_verified": newest, "warning": warning}


def get_settlements() -> dict:
    settlements = load_settlements()
    freshness = settlement_freshness()
    names = ", ".join(s["name"].split(" $")[0] for s in settlements[:3])
    return {
        "settlements": settlements,
        "data_freshness": freshness,
        "user_message": (
            f"I found {len(settlements)} open class-action settlements — including {names}. "
            + ("Note: my settlement list is stale, so please double-check deadlines before filing."
               if freshness["stale"]
               else "All verified within the last 90 days. Want me to scan your receipts for matches?")
        ),
    }


def _nudge_for(match: dict) -> str:
    s = match["settlement_name"]
    payout = sanitize(match.get("typical_payout_range", ""), 120)
    ev = sanitize(match.get("evidence_snippet", ""), 140)
    return (
        f"Your {ev or 'purchase history'} matches an open class-action settlement: {s}. "
        f"Typical payout: {payout or 'see the claim pack'}. "
        "Filing takes a few minutes. Want the pre-filled claim pack? One tap."
    )


def scan(source: str = "gmail", owner: str | None = None) -> dict:
    """Match receipts against open settlements. Emits settlement_match events."""
    source = source.lower()
    settlements = load_settlements()
    if source == "fixtures":
        candidates = matcher.search_fixtures(settlements)
    elif source == "gmail":
        candidates = matcher.search_gmail(settlements)
    else:
        raise ValueError("source must be 'gmail' or 'fixtures'")

    created = []
    for c in candidates:
        match_id = uuid.uuid4().hex[:12]
        match = {
            "id": match_id,
            "settlement_name": c["settlement_name"],
            "claim_deadline": c.get("claim_deadline", ""),
            "status": "candidate",
            "matched_keyword": c.get("matched_keyword", ""),
            "evidence_json": json.dumps({
                "snippet": c.get("snippet", ""),
                "from": c.get("from", ""),
                "subject": c.get("subject", ""),
                "date": c.get("date", ""),
                "message_id": c.get("id", ""),
            }),
            "typical_payout_range": c.get("typical_payout_range", ""),
            "official_claim_url": c.get("official_claim_url", ""),
        }
        db.save_match(match, owner_id=owner)
        created.append(match)
        life_events.emit("settlement_match", {
            "match_id": match_id,
            "settlement_name": match["settlement_name"],
            "claim_deadline": match["claim_deadline"],
            "matched_keyword": match["matched_keyword"],
            "evidence_snippet": c.get("snippet", ""),
        }, source="class-action-cash")

    if not created:
        user_message = ("I scanned your receipts against the open settlements and didn't find "
                        "any matches this time. I'll keep watching — new settlements open every week.")
    else:
        bits = []
        for m in created[:3]:
            bits.append(f"{m['settlement_name'].split(' $')[0]} (deadline {m['claim_deadline']})")
        user_message = (f"I found {len(created)} potential settlement match{'es' if len(created) != 1 else ''}: "
                        + "; ".join(bits) + ". Want me to prep the claim packs? One tap each.")

    return {"matches": created, "events_emitted": len(created),
            "source": source, "user_message": user_message}


def list_matches(owner: str | None = None) -> dict:
    matches = db.list_matches(owner)
    if not matches:
        msg = "No matches yet — run a scan and I'll check your receipts against every open settlement."
    else:
        msg = (f"You have {len(matches)} match{'es' if len(matches) != 1 else ''}: "
               + ", ".join(f"{m['settlement_name'].split(' $')[0]} ({m['status']})" for m in matches[:5]))
    return {"matches": matches, "user_message": msg}


def generate_claim_pack(match_id: str, full_name: str = "", email: str = "",
                        address: str = "", owner: str | None = None) -> dict:
    """Build the filing pack. The USER files the claim; the connector NEVER files."""
    match = _owned_match(match_id, owner)
    settlement = next((s for s in load_settlements()
                       if s["name"] == match["settlement_name"]), None)
    evidence = json.loads(match.get("evidence_json") or "{}")

    checklist = [
        "Confirm you meet the class definition in 'Who is eligible' below — do not file if you are unsure.",
        "Claims are filed under penalty of perjury. Filing a claim you are not entitled to is a false statement on a court-supervised form.",
        f"File before the claim deadline: {match.get('claim_deadline', 'see official site')}.",
        "Save a copy of your submitted claim and any confirmation code.",
    ]
    steps = [
        f"Open the official claim site: {settlement.get('official_claim_url') if settlement else match.get('official_claim_url', '')}",
        "Fill in your name and contact details exactly as in the pre-filled sheet below.",
        "When asked for the product/purchase, use the matched receipt details below.",
        "Choose your payment method (PayPal, Venmo, Zelle, check, or prepaid card where offered).",
        "Submit, save the confirmation code, and reply here with 'payout confirmed: $<amount>' when the money arrives.",
    ]
    pack = {
        "match_id": match_id,
        "settlement_name": sanitize(match["settlement_name"], 200),
        "claim_deadline": match.get("claim_deadline", ""),
        "official_claim_url": (settlement or {}).get("official_claim_url", match.get("official_claim_url", "")),
        "claim_url_note": (settlement or {}).get("claim_url_note", ""),
        "eligibility_summary": sanitize((settlement or {}).get("eligibility_summary", ""), 2000),
        "eligibility_checklist": [sanitize(x, 500) for x in checklist],
        "filing_steps": [sanitize(x, 500) for x in steps],
        "prefilled_info_sheet": {
            "full_name": sanitize(full_name, 200) or "[ask the user]",
            "email": sanitize(email, 200) or "[ask the user]",
            "mailing_address": sanitize(address, 300) or "[ask the user]",
            "matched_purchase": {
                "merchant_or_sender": sanitize(evidence.get("from", ""), 200),
                "subject": sanitize(evidence.get("subject", ""), 200),
                "date": sanitize(evidence.get("date", ""), 60),
                "details": sanitize(evidence.get("snippet", ""), 500),
            },
            "matched_keyword": sanitize(match.get("matched_keyword", ""), 100),
        },
        "important": ("Class Action Cash never files claims for you — you file directly on the "
                      "official settlement website above. Filing there is free."),
        "user_message": (
            f"Your claim pack for the {match['settlement_name'].split(' $')[0]} settlement is ready — "
            f"file before {match.get('claim_deadline', 'the deadline')} at the official site in the pack. "
            "You file it yourself; it takes a few minutes. Ping me when the payout lands and I'll handle my fee then."
        ),
    }
    db.update_match_status(match_id, "claim_pack_generated", owner)
    return pack


def receive_life_event(event_type: str, payload: dict, owner: str | None = None) -> dict:
    """Fan-out receiver. Creates a draft match and returns the proactive nudge."""
    if event_type != "settlement_match":
        return {"accepted": False, "event_type": event_type,
                "user_message": f"I don't handle '{event_type}' events — nothing to do here."}
    payload = payload or {}
    name = sanitize(payload.get("settlement_name", ""), 200)
    deadline = sanitize(payload.get("claim_deadline", ""), 20)
    kw = sanitize(payload.get("matched_keyword", ""), 100)
    snippet = sanitize(payload.get("evidence_snippet", ""), 500)
    match_id = uuid.uuid4().hex[:12]
    settlement = next((s for s in load_settlements() if s["name"] == name), None)
    match = {
        "id": match_id,
        "settlement_name": name or "Unknown settlement",
        "claim_deadline": deadline,
        "status": "triggered",
        "matched_keyword": kw,
        "evidence_json": json.dumps({"snippet": snippet,
                                     "source": "life-event fan-out"}),
        "typical_payout_range": (settlement or {}).get("typical_payout_range", ""),
        "official_claim_url": (settlement or {}).get("official_claim_url", ""),
    }
    db.save_match(match, owner_id=owner)
    nudge = _nudge_for({**match, "evidence_snippet": snippet})
    return {"accepted": True, "match_id": match_id, "status": "triggered",
            "user_message": nudge}


def setup_billing(match_id: str, name: str, email: str = "", owner: str | None = None) -> dict:
    match = _owned_match(match_id, owner)
    setup_key = f"{billing.CONNECTOR}-setup-{owner}-{match_id}"
    res = billing.setup_customer(name, email, idempotency_key=setup_key)
    if "error" in res:
        return {"ok": False, "error": res["error"],
                "user_message": "I couldn't set up billing just now — " + sanitize(res["error"], 200) +
                               ". Your card was not saved and nothing was charged."}
    si = billing.create_card_setup(res["customer_id"], idempotency_key=setup_key)
    if "error" in si:
        return {"ok": False, "error": si["error"],
                "user_message": "I couldn't create the secure card setup — " + sanitize(si["error"], 200) +
                               ". Nothing was charged."}
    db.save_billing({
        "match_id": match_id, "customer_name": name, "customer_email": email,
        "stripe_customer_id": res["customer_id"],
        "setup_intent_id": si.get("setup_intent_id", ""),
        "status": "card_pending",
    }, owner_id=owner)
    return {
        "ok": True,
        "match_id": match_id,
        "stripe_customer_id": res["customer_id"],
        "client_secret": si.get("client_secret"),
        "setup_intent_id": si.get("setup_intent_id"),
        "fee_disclosure": FEE_DISCLOSURE,
        "fee_rate": FEE_RATE,
        "user_message": (
            "Quick heads-up before you save your card: you will be charged 20% of the "
            "confirmed settlement payout, only if you confirm the payout. No charge otherwise. "
            "Complete the secure card setup and you're all set — then just file your claim and tell me when the money lands."
        ),
    }


def confirm_payout(match_id: str, amount: float, currency: str = "USD",
                   owner: str | None = None) -> dict:
    """User confirms the payout -> charge the 20% fee off-session.

    The charge always goes to the match's STORED stripe_customer_id — the
    request carries only the payout amount, never card details. The fee is
    20% of the asserted payout, sanity-bounded at 2x the settlement's
    published payout ceiling (parsed from the match's typical payout range
    when parseable): a larger assertion is rejected (400) rather than charged.
    """
    if amount is None or amount <= 0:
        raise ValueError("amount must be a positive number (the payout you received, in dollars).")
    match = _owned_match(match_id, owner)
    bill = db.get_billing(match_id, owner)
    if not bill or not bill.get("stripe_customer_id"):
        return {"ok": False, "error": "no_saved_card",
                "user_message": "I don't have a card on file for this match yet — set up billing first, then confirm the payout."}
    if bill.get("status") == "billed":
        return {"ok": False, "error": "already_billed",
                "user_message": "The 20% fee for this payout was already charged — nothing more to do. Enjoy the settlement!"}
    expected = _expected_payout_ceiling(match)
    if expected and amount > 2 * expected:
        raise ValueError(
            f"asserted payout ${amount:,.2f} is more than 2x the published payout range "
            f"for this settlement (up to ${expected:,.2f}) — please confirm the payout "
            "amount you actually received before I charge the fee.")
    fee_cents = int(round(amount * FEE_RATE * 100))
    desc = f"Class Action Cash fee: 20% of ${amount:,.2f} payout ({match['settlement_name'][:60]})"
    res = billing.charge_fee(bill["stripe_customer_id"], fee_cents, desc,
                             idempotency_key=f"{billing.CONNECTOR}-fee-{owner}-{match_id}")
    if "error" in res:
        return {"ok": False, "error": res["error"],
                "user_message": "The payout is confirmed — congrats! — but the fee charge didn't go through: "
                               + sanitize(res["error"], 200) + ". Nothing was charged; I'll retry or you can pay another way."}
    db.save_billing({**bill, "payment_intent_id": res.get("payment_intent_id"),
                     "payout_amount_cents": int(round(amount * 100)),
                     "fee_cents": fee_cents, "status": "billed"}, owner_id=owner)
    db.update_match_status(match_id, "billed", owner)
    return {
        "ok": True, "match_id": match_id, "currency": currency.upper(),
        "payout_amount": round(amount, 2), "fee_rate": FEE_RATE,
        "fee_charged": round(fee_cents / 100, 2),
        "payment_intent_id": res.get("payment_intent_id"),
        "dry_run": res.get("dry_run", False),
        "user_message": (
            f"You got paid ${amount:,.2f} from the {match['settlement_name'].split(' $')[0]} settlement — nice! "
            f"My 20% fee of ${fee_cents / 100:,.2f} was charged to your saved card. Thanks for using Class Action Cash."
        ),
    }
