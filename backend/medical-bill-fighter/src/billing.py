"""Stripe billing for the medical-bill-fighter connector.

Flow:
  1. Case created -> setup_customer() creates a Stripe customer, stored on the case.
  2. User saves a card -> create_card_setup() returns a SetupIntent client_secret;
     the Muse client collects the card against it (card stored for off-session use).
  3. User confirms a reduction -> confirm_reduction() charges the 25%
     contingency fee off-session via PaymentIntent.

Fee disclosure (shown BEFORE any card is saved, per /api/cases/{id}/billing/setup
response and README):
  "You will be charged 25% of the confirmed bill reduction, only if you confirm
   the reduction. No charge otherwise."

All Stripe calls go through the stripe skill CLI, which carries the
user-connected custom.stripe-billing credential. No raw keys here, in logs, or
in the database. Deployment should use a least-privilege restricted Stripe key
(scopes: Customers, SetupIntents, PaymentIntents write only — see README).
"""
import json
import os
import subprocess
from pathlib import Path

STRIPE_CLI = Path.home() / "workspace/skills/stripe/bin/stripe"
FEE_RATE = 0.25

CONNECTOR = "medical-bill-fighter"

FEE_DISCLOSURE = (
    "You will be charged 25% of the confirmed bill reduction, only if you "
    "confirm the reduction. No charge otherwise. You can cancel at any time "
    "before a charge is made."
)


STRIPE_API = "https://api.stripe.com/v1"


def _stripe_cli(*args: str) -> dict:
    """Dev transport: the local stripe skill CLI (agent machine only)."""
    proc = subprocess.run([str(STRIPE_CLI), *args], capture_output=True, text=True, timeout=60)
    out = proc.stdout.strip()
    try:
        data = json.loads(out[out.index("{"):]) if out else {}
    except (ValueError, IndexError):
        data = {"ok": False, "error": "unparseable stripe output: " + out[:200]}
    if proc.returncode != 0 and "ok" not in data:
        data = {"ok": False, "error": out[:300] or proc.stderr[:300]}
    return data


def _stripe_rest(*args: str, idempotency_key: str | None = None) -> dict:
    """Production transport: direct Stripe REST calls (STRIPE_SECRET_KEY set).

    Mirrors the stripe skill CLI's request shapes exactly (form-encoded POSTs).
    The key is read from the environment only and is never logged.

    idempotency_key is sent as the Idempotency-Key header so retried SetupIntent
    / PaymentIntent creates are de-duplicated by Stripe (24h window). The dev CLI
    transport has no idempotency support and ignores the key (see _stripe).
    """
    import requests

    key = os.environ.get("STRIPE_SECRET_KEY", "")
    cmd = args[0] if args else ""
    pairs = list(zip(args[1::2], args[2::2]))
    opts = dict(pairs)
    headers = {"Authorization": "Bearer " + key}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    try:
        if cmd == "create-customer":
            resp = requests.post(
                STRIPE_API + "/customers",
                data={"name": opts.get("--name", ""), "email": opts.get("--email", "")},
                headers=headers, timeout=30)
        elif cmd == "create-setup-intent":
            resp = requests.post(
                STRIPE_API + "/setup_intents",
                data={"customer": opts.get("--customer", "")},
                headers=headers, timeout=30)
        elif cmd == "charge":
            resp = requests.post(
                STRIPE_API + "/payment_intents",
                data={"customer": opts.get("--customer", ""),
                      "amount": opts.get("--amount-cents", "0"),
                      "currency": "usd",
                      "off_session": "true",
                      "confirm": "true",
                      "description": opts.get("--desc", "Contingency fee")},
                headers=headers, timeout=30)
        else:
            return {"ok": False, "error": "unsupported stripe command: " + cmd}
    except Exception as exc:
        return {"ok": False, "error": "stripe request failed: " + type(exc).__name__}
    if resp.status_code >= 400:
        return {"ok": False, "http_status": resp.status_code,
                "detail": resp.text[:500]}
    try:
        return resp.json()
    except ValueError:
        return {"ok": False, "error": "unparseable stripe response"}


def _stripe(*args: str, idempotency_key: str | None = None) -> dict:
    """Route Stripe calls: direct REST in production (STRIPE_SECRET_KEY set),
    local skill CLI on the dev machine otherwise.

    The dev CLI transport has no idempotency support — the key is accepted and
    silently ignored there so callers can plumb it uniformly. Production REST
    sends it as the Idempotency-Key header.
    """
    if os.environ.get("STRIPE_SECRET_KEY"):
        return _stripe_rest(*args, idempotency_key=idempotency_key)
    return _stripe_cli(*args)


def setup_customer(name: str, email: str = "") -> dict:
    """Create a Stripe customer for the patient. Returns {'customer_id': ...} or {'error': ...}."""
    args = ["create-customer", "--name", name]
    if email:
        args += ["--email", email]
    res = _stripe(*args)
    if res.get("id"):
        return {"customer_id": res["id"]}
    return {"error": res.get("error") or res.get("detail") or "customer creation failed"}


def create_card_setup(customer_id: str, idempotency_key: str | None = None) -> dict:
    """Create a SetupIntent so the patient can save a card for the later contingency charge."""
    res = _stripe("create-setup-intent", "--customer", customer_id,
                  idempotency_key=idempotency_key)
    if res.get("client_secret"):
        return {"client_secret": res["client_secret"], "setup_intent_id": res.get("id")}
    return {"error": res.get("error") or res.get("detail") or "setup intent failed"}


def charge_fee(customer_id: str, amount_cents: int, description: str,
               idempotency_key: str | None = None) -> dict:
    """Off-session charge of the contingency fee against the saved card.

    The fee amount is always derived server-side (contingency_cents) — the
    caller never takes an amount from the client. idempotency_key de-duplicates
    retried charges on the production transport.
    """
    res = _stripe("charge", "--customer", customer_id,
                  "--amount-cents", str(amount_cents), "--desc", description,
                  idempotency_key=idempotency_key)
    if res.get("id") and res.get("status") in ("succeeded", "requires_capture"):
        return {"payment_intent_id": res["id"], "status": res["status"],
                "amount_cents": res.get("amount")}
    return {"error": (res.get("error") or {}).get("message") if isinstance(res.get("error"), dict)
            else res.get("error") or res.get("detail") or f"charge failed (status={res.get('status')})",
            "payment_intent_id": res.get("id")}


def contingency_cents(reduction_amount: float) -> int:
    return int(round(reduction_amount * FEE_RATE * 100))
