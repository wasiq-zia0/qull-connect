"""eu261-flight-comp pricing and shared Stripe billing.

Card collection uses Stripe-hosted Checkout in setup mode. A saved card is not a
payment: callers must later present the exact fee and record explicit consent.
"""
from pathlib import Path
import sys

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
# In source checkouts the common package is a sibling of the service. Deployment
# copies the same package into each service root; no network import is involved.
if not (_SERVICE_ROOT / "payment_support").is_dir():
    sys.path.insert(0, str(_SERVICE_ROOT.parent))
from payment_support import BillingClient, percent_cents

CONNECTOR = 'eu261-flight-comp'
CURRENCY = 'eur'
FEE_RATE = 0.30

FEE_DISCLOSURE = '30% of the compensation you confirm receiving, charged in EUR. No recovery, no fee.'
_client = BillingClient(CONNECTOR, _SERVICE_ROOT / "data", currency=CURRENCY, disclosure=FEE_DISCLOSURE)


def setup_customer(name: str, email: str = "", idempotency_key: str | None = None) -> dict:
    return _client.setup_customer(name, email, idempotency_key)


def create_card_setup(customer_id: str, idempotency_key: str | None = None) -> dict:
    return _client.create_card_setup(customer_id, idempotency_key)


def retrieve_card_setup(customer_id: str, setup_intent_id: str | None = None,
                        checkout_session_id: str | None = None) -> dict:
    return _client.retrieve_card_setup(customer_id, setup_intent_id, checkout_session_id)


def charge_fee(customer_id: str, amount_cents: int, description: str,
               idempotency_key: str | None = None, *, setup_intent_id: str | None = None,
               checkout_session_id: str | None = None, consent: bool = False) -> dict:
    return _client.charge_fee(customer_id, amount_cents, description, idempotency_key,
                              setup_intent_id=setup_intent_id, checkout_session_id=checkout_session_id,
                              consent=consent)


def fee_cents(amount_eur: float) -> int:
    return percent_cents(amount_eur, "0.30")
