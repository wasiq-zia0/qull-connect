"""Read-only by default operator recovery: python -m payment_support --help."""
import argparse
import json
from pathlib import Path

from .service import BillingClient


def main():
    parser = argparse.ArgumentParser(description="Verify and optionally link an existing Stripe payment to its original durable Qull ledger operation. Never creates or confirms a payment.")
    parser.add_argument("--connector", required=True)
    parser.add_argument("--data-dir", type=Path, default=Path.cwd() / "data")
    parser.add_argument("--operation", required=True, help="64-character ledger operation hash from Stripe PaymentIntent metadata or the ledger")
    parser.add_argument("--payment-intent", required=True, help="Existing provider PaymentIntent ID")
    parser.add_argument("--apply", action="store_true", help="Persist the verified association; default only verifies")
    args = parser.parse_args()
    result = BillingClient(args.connector, args.data_dir).reconcile_payment(args.operation, args.payment_intent, apply=args.apply)
    print(json.dumps(result, indent=2))
    raise SystemExit(1 if result.get("error") else 0)


if __name__ == "__main__":
    main()
