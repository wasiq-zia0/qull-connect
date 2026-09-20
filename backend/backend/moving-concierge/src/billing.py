"""Stripe billing for the moving-concierge connector.

Flow:
  1. Move intake confirmed -> setup_customer() creates a Stripe customer, stored on the move.
  2. User saves a card -> create_card_setup() returns a SetupIntent client_secret;
     the agent/client collects the card against it (card stored for off-session use).
  3. User confirms -> charge_pack() charges the flat $49.00 fee off-session.

The fee is FLAT $49.00 per move — no contingency, no meter. Every response that
touches money carries the plain-language disclosure in FEE_DISCLOSURE.

All Stripe calls go through the stripe skill CLI, which carries the
user-connected custom.stripe-billing credential. No raw keys here. Production transport: when STRIPE_SECRET_KEY is set, calls go direct to https://api.stripe.com via REST; the local skill CLI is the dev fallback. The key is read from the environment only and is never logged.
"""
import json
import os
import subprocess
from pathlib import Path

STRIPE_CLI = Path.home() / "workspace/skills/stripe/bin/stripe"

CONNECTOR = "moving-concierge"

FEE_CENTS = 4900
FEE_LABEL = "$49.00"
FEE_DISCLOSURE = (
    "You will be charged a flat $49.00 for the complete address-change pack. "
    "Charged once, after you confirm."
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
    """Create a Stripe customer for the move. Returns {'customer_id': ...} or {'error': ...}."""
    args = ["create-customer", "--name", name]
    if email:
        args += ["--email", email]
    res = _stripe(*args)
    if res.get("id"):
        return {"customer_id": res["id"]}
    return {"error": res.get("error") or res.get("detail") or "customer creation failed"}


def create_card_setup(customer_id: str, idempotency_key: str | None = None) -> dict:
    """Create a SetupIntent so the mover can save a card for the later flat-fee charge."""
    res = _stripe("create-setup-intent", "--customer", customer_id,
                  idempotency_key=idempotency_key)
    if res.get("client_secret"):
        return {"client_secret": res["client_secret"], "setup_intent_id": res.get("id")}
    return {"error": res.get("error") or res.get("detail") or "setup intent failed"}


def charge_pack(customer_id: str, idempotency_key: str | None = None) -> dict:
    """Off-session charge of the flat $49.00 pack fee against the saved card.

    The amount is a server-side constant (FEE_CENTS) — never client input.
    idempotency_key de-duplicates retried charges on the production transport.
    """
    res = _stripe("charge", "--customer", customer_id,
                  "--amount-cents", str(FEE_CENTS),
                  "--desc", "Moving Concierge address-change pack (flat fee)",
                  idempotency_key=idempotency_key)
    if res.get("id") and res.get("status") in ("succeeded", "requires_capture"):
        return {"payment_intent_id": res["id"], "status": res["status"],
                "amount_cents": res.get("amount"), "amount_label": FEE_LABEL}
    return {"error": (res.get("error") or {}).get("message") if isinstance(res.get("error"), dict)
            else res.get("error") or res.get("detail") or f"charge failed (status={res.get('status')})",
            "payment_intent_id": res.get("id")}
