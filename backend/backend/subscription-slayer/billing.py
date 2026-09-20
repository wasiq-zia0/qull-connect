"""Stripe billing for the subscription-slayer connector.

Flow:
  1. User sets up billing -> setup_customer() creates a Stripe customer,
     create_card_setup() returns a SetupIntent client_secret so the user can
     save a card for later off-session use. No charge is taken at this point.
  2. User confirms savings -> confirm_savings() charges $10 per completed
     cancellation off-session via PaymentIntent.

HONEST FEE DISCLOSURE (shown before the user saves a card):
  "You will be charged $10 for each subscription cancellation you confirm —
   charged once, after you confirm. No charge otherwise."

All Stripe calls go through the stripe skill CLI at
~/workspace/skills/stripe/bin/stripe, which carries the user-connected
custom.stripe-billing credential. No raw keys in this code. Production transport: when STRIPE_SECRET_KEY is set, calls go direct to https://api.stripe.com via REST; the local skill CLI is the dev fallback. The key is read from the environment only and is never logged.
"""
import json
import os
import subprocess
from pathlib import Path

STRIPE_CLI = Path.home() / "workspace/skills/stripe/bin/stripe"

# Flat fee: $10 per completed cancellation the user confirms.
FEE_PER_CANCELLATION_CENTS = 1000

FEE_DISCLOSURE = (
    "You will be charged $10 for each subscription cancellation you confirm "
    "— charged once, after you confirm. No charge otherwise."
)

# Deterministic Stripe idempotency keys are built as
# f"{CONNECTOR}-setup-{owner}-{record_id}" / f"{CONNECTOR}-fee-{owner}-{record_id}"
# by the callers, so a retried setup/charge can never double-bill.
CONNECTOR = "subscription-slayer"


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

    ``idempotency_key`` is sent as the ``Idempotency-Key`` header on
    SetupIntent/PaymentIntent creations so a retried request can never
    create a second intent or charge.
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

    Note: the dev CLI fallback has no idempotency-key flag, so the key is
    ignored there (documented); the REST transport always sends it.
    """
    if os.environ.get("STRIPE_SECRET_KEY"):
        return _stripe_rest(*args, idempotency_key=idempotency_key)
    return _stripe_cli(*args)


def setup_customer(name: str, email: str = "") -> dict:
    """Create a Stripe customer. Returns {'customer_id': ...} or {'error': ...}."""
    args = ["create-customer", "--name", name]
    if email:
        args += ["--email", email]
    res = _stripe(*args)
    if res.get("id"):
        return {"customer_id": res["id"]}
    return {"error": res.get("error") or res.get("detail") or "customer creation failed"}


def create_card_setup(customer_id: str, idempotency_key: str | None = None) -> dict:
    """Create a SetupIntent so the user can save a card for the later fee charge."""
    res = _stripe("create-setup-intent", "--customer", customer_id,
                  idempotency_key=idempotency_key)
    if res.get("client_secret"):
        return {"client_secret": res["client_secret"], "setup_intent_id": res.get("id")}
    return {"error": res.get("error") or res.get("detail") or "setup intent failed"}


def charge_fee(customer_id: str, amount_cents: int, description: str,
               idempotency_key: str | None = None) -> dict:
    """Off-session charge of the per-cancellation fee against the saved card."""
    res = _stripe("charge", "--customer", customer_id,
                  "--amount-cents", str(amount_cents), "--desc", description,
                  idempotency_key=idempotency_key)
    if res.get("id") and res.get("status") in ("succeeded", "requires_capture"):
        return {"payment_intent_id": res["id"], "status": res["status"],
                "amount_cents": res.get("amount")}
    err = res.get("error")
    err_msg = err.get("message") if isinstance(err, dict) else err
    return {"error": err_msg or res.get("detail") or f"charge failed (status={res.get('status')})",
            "payment_intent_id": res.get("id")}


def fee_cents(n_cancellations: int) -> int:
    """$10 flat per completed cancellation the user confirms, in cents."""
    if n_cancellations <= 0:
        return 0
    return FEE_PER_CANCELLATION_CENTS * n_cancellations


def first_year_savings(subscriptions: list[dict]) -> float:
    """Sum of monthly*12 for monthly subs + full yearly amounts for yearly subs.

    Only counts subscriptions with a positive confirmed savings_monthly.
    Kept as an informational figure in the confirm response; the fee itself
    is the $10 flat per cancellation.
    """
    total = 0.0
    for s in subscriptions:
        monthly = s.get("savings_monthly") or 0
        if monthly <= 0:
            continue
        if s.get("frequency") == "yearly":
            total += float(monthly)  # stored as the yearly amount for yearly subs
        else:
            total += float(monthly) * 12
    return round(total, 2)
