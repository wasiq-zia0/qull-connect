"""Owner-scoped settlement matching, filing preparation and explicit-fee billing."""
import hashlib
import json
import math
import os
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import urlsplit
import db
import matcher
import billing
from safety import sanitize

SETTLEMENTS_FILE = Path(__file__).parent / "data/open_settlements.json"
FEE_RATE = 0.20
FRESHNESS_STALE_DAYS = 90
FEE_DISCLOSURE = "The fee is 20% of a settlement payout you actually receive, in USD. No recovery means no fee. A fee is charged only after you explicitly approve its exact amount. You file your claim yourself; filing directly with the administrator is free."


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def _owned_match(match_id, owner):
    if not owner:
        raise ValueError("An authenticated owner is required")
    match = db.get_match(match_id, owner)
    if not match:
        raise KeyError("Unknown match")
    return match


def load_settlements():
    return json.loads(SETTLEMENTS_FILE.read_text())


def _active_settlements():
    today = date.today()
    active = []
    for row in load_settlements():
        try:
            age = (today - date.fromisoformat(row["last_verified"])).days
            valid = (row.get("review_status") == "official_administrator_verified"
                     and row.get("official_claim_url_verified") is True
                     and urlsplit(row["official_claim_url"]).scheme == "https"
                     and 0 <= age <= FRESHNESS_STALE_DAYS
                     and date.fromisoformat(row["claim_deadline"]) >= today)
        except (KeyError, ValueError, TypeError):
            valid = False
        if valid:
            active.append(row)
    return active


def settlement_freshness():
    active = _active_settlements()
    return {"active_verified_count": len(active), "excluded_count": len(load_settlements()) - len(active),
            "stale": not bool(active), "warning": "Deadlines and eligibility must be confirmed with each official administrator before filing. Unverified, expired and stale entries are excluded."}


def get_settlements():
    settlements = _active_settlements()
    return {"settlements": settlements, "data_freshness": settlement_freshness(),
            "user_message": f"{len(settlements)} source-verified, unexpired settlement opportunities are available. Receipt matches identify candidates; they do not establish eligibility."}


def scan(source="receipts", owner=None, receipts=None):
    if not owner:
        raise ValueError("An authenticated owner is required")
    active = _active_settlements()
    demo = source == "fixtures"
    if demo:
        if os.environ.get("ENV") not in ("test", "development") or os.environ.get("ALLOW_DEMO_FIXTURES") != "1":
            raise ValueError("Demo fixtures are disabled outside explicit local development/test mode")
        candidates = matcher.search_fixtures(active)
    elif source == "receipts":
        if not receipts:
            raise ValueError("Supply at least one receipt excerpt")
        candidates = matcher.search_receipts(active, receipts)
    else:
        raise ValueError("Gmail OAuth is not implemented. Use source='receipts' with your own receipt excerpts.")
    created = []
    for candidate in candidates:
        evidence = {"snippet": candidate["snippet"], "from": candidate["from"], "subject": candidate["subject"],
                    "date": candidate["date"], "message_id": candidate["id"], "demo": demo}
        digest = hashlib.sha256(json.dumps([owner, candidate["settlement_name"], demo], sort_keys=True).encode()).hexdigest()
        existing = db.get_match(digest, owner)
        if existing:
            saved = json.loads(existing.get("evidence_json") or "{}")
            prior = saved.get("receipts", [saved])
            if evidence not in prior:
                saved = {"receipts": [*prior, evidence], "demo": demo}
                existing["evidence_json"] = json.dumps(saved)
                db.save_match(existing, owner_id=owner)
            if not any(row["id"] == digest for row in created):
                created.append(existing)
            continue
        match = {"id": digest, "settlement_name": candidate["settlement_name"], "claim_deadline": candidate["claim_deadline"],
                 "status": "candidate", "matched_keyword": candidate["matched_keyword"], "evidence_json": json.dumps({"receipts": [evidence], "demo": demo}),
                 "typical_payout_range": candidate["typical_payout_range"], "official_claim_url": candidate["official_claim_url"]}
        db.save_match(match, owner_id=owner)
        created.append(match)
    return {"matches": created, "source": source, "demo": demo, "events_emitted": 0,
            "user_message": f"Found {len(created)} potential matches in the receipt excerpts you supplied. Review each class definition and confirm eligibility before requesting a filing pack. No claim has been filed."}


def list_matches(owner=None):
    if not owner:
        raise ValueError("An authenticated owner is required")
    matches = db.list_matches(owner)
    return {"matches": matches, "user_message": f"You have {len(matches)} saved candidate matches. No monitoring or automatic filing is scheduled."}


def _active_match(match):
    settlement = next((s for s in _active_settlements() if s["name"] == match["settlement_name"]), None)
    if settlement is None:
        raise ValueError("This settlement is expired, stale or unverified. Confirm current information with its administrator before proceeding.")
    return settlement


def generate_claim_pack(match_id, full_name="", email="", address="", owner=None, eligibility_confirmed=False):
    match = _owned_match(match_id, owner)
    settlement = _active_match(match)
    if eligibility_confirmed is not True:
        raise ValueError("Review the official class definition and explicitly confirm eligibility first")
    evidence = json.loads(match.get("evidence_json") or "{}")
    pack = {"match_id": match_id, "settlement_name": settlement["name"], "claim_deadline": settlement["claim_deadline"],
            "official_claim_url": settlement["official_claim_url"], "eligibility_summary": settlement["eligibility_summary"],
            "prefilled_info_sheet": {"full_name": sanitize(full_name, 200), "email": sanitize(email, 200),
                                     "mailing_address": sanitize(address, 300), "receipt_evidence": evidence},
            "filing_steps": ["Read the official notice, exclusions and proof requirements.",
                             "Open the official administrator site and use its claim form.",
                             "Enter accurate details and provide the required evidence. Never invent purchases or attestations.",
                             "Submit yourself before the deadline, then keep the confirmation.",
                             "Return only after payment is received to review the optional-service fee."],
            "important": "This connector prepares information; it does not file claims or determine legal eligibility. Direct filing is free.",
            "user_message": "Your filing information is ready. Review the official notice and complete the administrator's form yourself. Nothing has been submitted."}
    db.update_match_status(match_id, "claim_pack_generated", owner)
    return pack


def receive_life_event(event_type, payload, owner=None):
    if not owner:
        raise ValueError("An authenticated owner is required")
    if event_type != "settlement_match":
        return {"accepted": False, "user_message": "This connector handles settlement_match events only."}
    # A caller supplies receipt evidence, not an arbitrary settlement/owner record.
    from app import ReceiptIn, _validate_input
    receipts = payload.get("receipts")
    if set(payload) != {"receipts"} or not isinstance(receipts, list) or not 1 <= len(receipts) <= 100:
        raise ValueError("Supply receipts (1-100) in the event payload")
    validated = [_validate_input(ReceiptIn, r).model_dump(mode="json") for r in receipts]
    return {"accepted": True, **scan("receipts", owner, validated)}


def setup_billing(match_id, name, email="", owner=None, accept_fee_terms=False):
    match = _owned_match(match_id, owner)
    # Payment can arrive after the filing deadline. An existing owner-scoped
    # match remains billable after expiry; only new matches/packs require an active listing.
    if json.loads(match.get("evidence_json") or "{}").get("demo"):
        raise ValueError("Demo matches cannot be billed")
    if accept_fee_terms is not True:
        raise ValueError("Accept the fee terms before opening Stripe setup")
    bill = db.get_billing(match_id, owner) or {"match_id": match_id}
    if bill.get("status") == "billed":
        raise ValueError("This fee is already settled")
    key = f"{billing.CONNECTOR}-setup-{owner}-{match_id}"
    customer_id = bill.get("stripe_customer_id")
    if not customer_id:
        result = billing.setup_customer(name, email, idempotency_key=key)
        if "error" in result:
            return {"ok": False, **result, "user_message": result["error"]}
        customer_id = result["customer_id"]
        bill = {**bill, "customer_name": name, "customer_email": email, "stripe_customer_id": customer_id}
        db.save_billing(bill, owner_id=owner)
    setup = billing.create_card_setup(customer_id, idempotency_key=key)
    if "error" in setup:
        return {"ok": False, **setup, "user_message": setup["error"]}
    db.save_billing({**bill, "checkout_session_id": setup.get("checkout_session_id"), "setup_intent_id": setup.get("setup_intent_id"),
                     "fee_terms_accepted_at": _utcnow(), "status": "card_pending"}, owner_id=owner)
    return {"ok": True, "match_id": match_id, "setup_url": setup.get("setup_url"),
            "checkout_session_id": setup.get("checkout_session_id"), "fee_disclosure": FEE_DISCLOSURE,
            "user_message": FEE_DISCLOSURE + " Open setup_url to save a payment method securely with Stripe. Nothing is charged now."}


def confirm_payout(match_id, amount, currency="USD", owner=None, confirm_fee=False, fee_amount_cents=None):
    if not math.isfinite(amount) or amount <= 0 or amount > 1_000_000 or currency.upper() != "USD":
        raise ValueError("A finite positive USD payout up to 1000000 is required")
    match = _owned_match(match_id, owner)
    if json.loads(match.get("evidence_json") or "{}").get("demo"):
        raise ValueError("Demo matches cannot be billed")
    bill = db.get_billing(match_id, owner)
    if not bill or not bill.get("fee_terms_accepted_at"):
        raise ValueError("Accept fee terms and complete billing setup first")
    if bill.get("status") == "billed":
        raise ValueError("This fee is already settled")
    cents = int((Decimal(str(amount)) * Decimal("20")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if confirm_fee is not True or fee_amount_cents != cents:
        raise ValueError(f"Review and explicitly confirm the exact fee: {cents} cents USD")
    db.save_billing({**bill, "fee_confirmed_at": _utcnow()}, owner_id=owner)
    result = billing.charge_fee(bill["stripe_customer_id"], cents,
        f"Class Action Cash 20% fee, match {match_id}",
        idempotency_key=f"{billing.CONNECTOR}-fee-{hashlib.sha256(json.dumps([owner, match['settlement_name']]).encode()).hexdigest()}",
        setup_intent_id=bill.get("setup_intent_id"), checkout_session_id=bill.get("checkout_session_id"), consent=confirm_fee)
    if "error" in result or result.get("status") != "succeeded":
        return {"ok": False, **result, "user_message": result.get("error", "Payment has not been confirmed. Check its status before retrying.")}
    db.save_billing({**bill, "fee_confirmed_at": _utcnow(), "payment_intent_id": result["payment_intent_id"],
        "payout_amount_cents": int((Decimal(str(amount)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
        "fee_cents": cents, "status": "billed"}, owner_id=owner)
    db.update_match_status(match_id, "billed", owner)
    return {"ok": True, "match_id": match_id, "currency": "USD", "payout_amount": amount,
            "fee_rate": FEE_RATE, "fee_charged": cents / 100, "payment_intent_id": result["payment_intent_id"],
            "user_message": f"The fee of ${cents/100:.2f} was paid after your confirmation of a ${amount:.2f} payout."}
