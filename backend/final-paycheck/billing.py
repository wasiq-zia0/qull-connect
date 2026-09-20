"""Stripe billing for the final-paycheck connector.

Flow:
  1. Case created -> setup_customer() creates a Stripe customer, stored on the case.
  2. Employee saves a card -> create_card_setup() returns a SetupIntent client_secret;
     the frontend/connector collects the card against it (card stored for off-session use).
  3. Employee confirms recovery -> confirm_recovery() charges the 25% contingency fee
     off-session via PaymentIntent.

All Stripe calls go through the stripe skill CLI, which carries the
user-connected custom.stripe-billing credential. No raw keys here. Production transport: when STRIPE_SECRET_KEY is set, calls go direct to https://api.stripe.com via REST; the local skill CLI is the dev fallback. The key is read from the environment only and is never logged.

FINAL_PAYCHECK_STRIPE_MOCK=1 stubs all CLI calls (used by tests and dry runs).
"""
import json
import os
import subprocess
from pathlib import Path

STRIPE_CLI = Path.home() / "workspace/skills/stripe/bin/stripe"
FEE_RATE = 0.25
CONNECTOR = "final-paycheck"
MOCK = os.environ.get("FINAL_PAYCHECK_STRIPE_MOCK") == "1"


def _mock(*args: str) -> dict:
    cmd = args[0] if args else ""
    if cmd == "create-customer":
        return {"ok": True, "id": "cus_mock_finalpay", "mock": True}
    if cmd == "create-setup-intent":
        return {"ok": True, "id": "seti_mock_finalpay",
                "client_secret": "seti_mock_finalpay_secret_mock", "mock": True}
    if cmd == "charge":
        return {"ok": True, "id": "pi_mock_finalpay", "status": "succeeded",
                "amount": 0, "mock": True}
    return {"ok": False, "error": f"unknown mock command: {cmd}"}


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

    NOTE: idempotency_key is honored by the REST transport only; the MOCK
    and local skill CLI paths have no idempotency-key support and ignore it
    (the app-level already-charged guard still protects those paths).
    """
    if MOCK:
        return _mock(*args)
    if os.environ.get("STRIPE_SECRET_KEY"):
        return _stripe_rest(*args, idempotency_key=idempotency_key)
    return _stripe_cli(*args)


def setup_customer(name: str, email: str = "", idempotency_key: str | None = None) -> dict:
    """Create a Stripe customer for the employee. Returns {'customer_id': ...} or {'error': ...}."""
    args = ["create-customer", "--name", name]
    if email:
        args += ["--email", email]
    res = _stripe(*args, idempotency_key=idempotency_key)
    if res.get("id"):
        return {"customer_id": res["id"], "mock": res.get("mock", False)}
    return {"error": res.get("error") or res.get("detail") or "customer creation failed"}


def create_card_setup(customer_id: str, idempotency_key: str | None = None) -> dict:
    """Create a SetupIntent so the employee can save a card for the later contingency charge."""
    res = _stripe("create-setup-intent", "--customer", customer_id,
                  idempotency_key=idempotency_key)
    if res.get("client_secret"):
        return {"client_secret": res["client_secret"],
                "setup_intent_id": res.get("id"),
                "mock": res.get("mock", False)}
    return {"error": res.get("error") or res.get("detail") or "setup intent failed"}


def charge_fee(customer_id: str, amount_cents: int, description: str,
               idempotency_key: str | None = None) -> dict:
    """Off-session charge of the contingency fee against the saved card."""
    res = _stripe("charge", "--customer", customer_id,
                  "--amount-cents", str(amount_cents), "--desc", description,
                  idempotency_key=idempotency_key)
    if res.get("id") and res.get("status") in ("succeeded", "requires_capture"):
        return {"payment_intent_id": res["id"], "status": res.get("status"),
                "amount_cents": res.get("amount"), "mock": res.get("mock", False)}
    return {"error": (res.get("error") or {}).get("message") if isinstance(res.get("error"), dict)
            else res.get("error") or res.get("detail") or f"charge failed (status={res.get('status')})",
            "payment_intent_id": res.get("id")}


def contingency_cents(amount_recovered: float) -> int:
    return int(round(amount_recovered * FEE_RATE * 100))
