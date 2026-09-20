#!/usr/bin/env python3
"""Exercise shared billing against Stripe test mode using synthetic customers.

Requires STRIPE_MODE=test and a test key in STRIPE_SECRET_KEY. Never accepts live
credentials. Creates test customers, Checkout sessions, SetupIntents and simulated
payments; no real money moves. Browser checkout and bank authentication are separate
interactive checks. Output contains no credentials, client secrets or payment URLs.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from payment_support import BillingClient


def run():
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if os.environ.get("STRIPE_MODE") != "test" or not key.startswith(("sk_test_", "rk_test_", "rkcs_test_")):
        raise SystemExit("Refusing to run: an isolated Stripe test-mode key is required.")
    # Stripe's requests transport sets its own CA bundle explicitly. Honor an
    # operator-provided trusted bundle for managed test environments; TLS
    # verification remains enabled. Production transport uses the SDK default.
    if os.environ.get("REQUESTS_CA_BUNDLE"):
        import stripe
        bundle = Path(os.environ["REQUESTS_CA_BUNDLE"])
        if not bundle.is_file():
            raise SystemExit("Configured trusted certificate bundle does not exist.")
        stripe.ca_bundle_path = str(bundle)
    run_id = uuid.uuid4().hex
    checks = []
    def check(name, condition):
        checks.append({"check": name, "passed": bool(condition)})
        if not condition:
            raise RuntimeError(f"Sandbox check failed: {name}")

    with tempfile.TemporaryDirectory(prefix="qull-stripe-test-") as directory:
        os.environ["DATA_DIR"] = directory
        os.environ["BILLING_LEDGER_PATH"] = str(Path(directory) / "ledger.db")
        for currency in ("usd", "eur"):
            billing = BillingClient("integration-review-" + currency, Path(directory), currency=currency)
            api = billing._client()
            customer = billing.setup_customer("Qull synthetic integration test", idempotency_key=run_id + currency + "customer")
            check(currency + ": customer creation", bool(customer.get("customer_id")))
            cid = customer["customer_id"]
            hosted = billing.create_card_setup(cid, idempotency_key=run_id + currency + "setup")
            check(currency + ": hosted setup URL", bool(hosted.get("setup_url", "").startswith("https://checkout.stripe.com/")))
            check(currency + ": setup does not charge", hosted.get("charged") is False)
            pending = billing.retrieve_card_setup(cid, checkout_session_id=hosted["checkout_session_id"])
            check(currency + ": incomplete checkout is not a saved method", pending.get("code") == "setup_incomplete")
            # Stripe's official test PaymentMethod is confirmed through a separate
            # SetupIntent, exercising transport without simulating a browser click.
            setup = api.v1.setup_intents.create({
                "customer": cid, "payment_method": "pm_card_visa", "confirm": True,
                "usage": "off_session", "automatic_payment_methods": {"enabled": True, "allow_redirects": "never"},
            }, options={"idempotency_key": run_id + currency + "si"}).to_dict()
            check(currency + ": provider setup succeeded", setup.get("status") == "succeeded")
            verified = billing.retrieve_card_setup(cid, setup_intent_id=setup["id"])
            check(currency + ": method ownership verified", verified.get("card_saved") is True)
            kwargs = {"setup_intent_id": setup["id"], "idempotency_key": run_id + currency + "fee"}
            refused = billing.charge_fee(cid, 123, "Synthetic test fee", **kwargs)
            check(currency + ": missing consent refused", refused.get("code") == "consent_required")
            paid = billing.charge_fee(cid, 123, "Synthetic test fee", consent=True, **kwargs)
            check(currency + ": simulated fee succeeded", paid.get("status") == "succeeded" and not paid.get("error"))
            check(currency + ": currency preserved", paid.get("currency") == currency)
            replay = billing.charge_fee(cid, 123, "Synthetic test fee", consent=True, **kwargs)
            check(currency + ": retry reuses payment", replay.get("payment_intent_id") == paid["payment_intent_id"])
            conflict = billing.charge_fee(cid, 124, "Synthetic test fee", consent=True, **kwargs)
            check(currency + ": changed amount refused", conflict.get("code") == "billing_conflict")
    result = {
        "checked_at": datetime.now(timezone.utc).isoformat(), "environment": "isolated Stripe sandbox",
        "real_money_moved": False, "scope": "Shared billing SDK/Stripe integration in USD and EUR; browser completion and production deployment not covered",
        "checks": checks,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    run()
