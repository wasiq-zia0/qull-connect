"""Stripe billing for the class-action-cash connector.

Flow:
  1. Match created -> setup_customer() creates a Stripe customer, stored on the match.
  2. User saves a card -> create_card_setup() returns a SetupIntent client_secret;
     the frontend/connector collects the card against it (card stored for off-session use).
  3. User confirms payout -> confirm payout charges the 20% fee
     off-session via PaymentIntent.

All Stripe calls go through the stripe skill CLI, which carries the
user-connected custom.stripe-billing credential. No raw keys here. Production transport: when STRIPE_SECRET_KEY is set, calls go direct to https://api.stripe.com via REST; the local skill CLI is the dev fallback. The key is read from the environment only and is never logged.

Deployment note (review): production must use a restricted Stripe secret key
scoped to Customers, SetupIntents, and PaymentIntents write only.

TESTING: set CLASS_ACTION_CASH_DRY_RUN=1 to simulate Stripe responses without
ever invoking the CLI. Real charges never happen in tests.
"""
import json
import os
import subprocess
from pathlib import Path

STRIPE_CLI = Path.home() / "workspace/skills/stripe/bin/stripe"
FEE_RATE = 0.20
CONNECTOR = "class-action-cash"
DRY_RUN = os.environ.get("CLASS_ACTION_CASH_DRY_RUN") == "1"


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

    idempotency_key, when given, is sent as the Stripe Idempotency-Key
    header so a retried request can never create a duplicate SetupIntent
    or PaymentIntent.
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

    NOTE: idempotency_key is honored by the REST transport only; the DRY_RUN
    and local skill CLI paths have no idempotency-key support and ignore it
    (the app-level already-billed guard still protects those paths).
    """
    if DRY_RUN:
        return {"dry_run": True, "id": f"sim_{abs(hash(args)) % 10_000_000:07d}",
                "client_secret": "sim_secret", "status": "succeeded",
                "amount": int(args[args.index('--amount-cents') + 1]) if "--amount-cents" in args else None}
    if os.environ.get("STRIPE_SECRET_KEY"):
        return _stripe_rest(*args, idempotency_key=idempotency_key)
    return _stripe_cli(*args)


def setup_customer(name: str, email: str = "", idempotency_key: str | None = None) -> dict:
    """Create a Stripe customer. Returns {'customer_id': ...} or {'error': ...}."""
    args = ["create-customer", "--name", name]
    if email:
        args += ["--email", email]
    res = _stripe(*args, idempotency_key=idempotency_key)
    if res.get("id") or res.get("dry_run"):
        return {"customer_id": res.get("id") or f"cus_dryrun_{res['id']}",
                "dry_run": res.get("dry_run", False)}
    return {"error": res.get("error") or res.get("detail") or "customer creation failed"}


def create_card_setup(customer_id: str, idempotency_key: str | None = None) -> dict:
    """Create a SetupIntent so the user can save a card for the later fee charge."""
    res = _stripe("create-setup-intent", "--customer", customer_id,
                  idempotency_key=idempotency_key)
    if res.get("client_secret") or res.get("dry_run"):
        return {"client_secret": res.get("client_secret"),
                "setup_intent_id": res.get("id"),
                "dry_run": res.get("dry_run", False)}
    return {"error": res.get("error") or res.get("detail") or "setup intent failed"}


def charge_fee(customer_id: str, amount_cents: int, description: str,
               idempotency_key: str | None = None) -> dict:
    """Off-session charge of the 20% fee against the saved card."""
    res = _stripe("charge", "--customer", customer_id,
                  "--amount-cents", str(amount_cents), "--desc", description,
                  idempotency_key=idempotency_key)
    if res.get("dry_run"):
        return {"payment_intent_id": res["id"], "status": "succeeded",
                "amount_cents": res.get("amount"), "dry_run": True}
    if res.get("id") and res.get("status") in ("succeeded", "requires_capture"):
        return {"payment_intent_id": res["id"], "status": res["status"],
                "amount_cents": res.get("amount")}
    return {"error": (res.get("error") or {}).get("message") if isinstance(res.get("error"), dict)
            else res.get("error") or res.get("detail") or f"charge failed (status={res.get('status')})",
            "payment_intent_id": res.get("id")}


def fee_cents(amount: float) -> int:
    return int(round(amount * FEE_RATE * 100))
