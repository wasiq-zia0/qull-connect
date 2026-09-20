"""subscription-slayer pricing and shared Stripe billing.

Card collection uses Stripe-hosted Checkout in setup mode. A saved card is not a
payment: callers must later present the exact fee and record explicit consent.
"""
from pathlib import Path
import sys

_SERVICE_ROOT = Path(__file__).resolve().parent
# In source checkouts the common package is a sibling of the service. Deployment
# copies the same package into each service root; no network import is involved.
if not (_SERVICE_ROOT / "payment_support").is_dir():
    sys.path.insert(0, str(_SERVICE_ROOT.parent))
from payment_support import BillingClient, percent_cents

CONNECTOR = 'subscription-slayer'
CURRENCY = 'usd'
FEE_PER_CANCELLATION_CENTS = 1000

FEE_DISCLOSURE = '$10 for each completed cancellation you explicitly confirm. No completed cancellation, no fee.'
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


def fee_cents(n_cancellations: int) -> int:
    if isinstance(n_cancellations, bool) or not isinstance(n_cancellations, int) or n_cancellations < 0:
        raise ValueError("Cancellation count must be a nonnegative integer.")
    return FEE_PER_CANCELLATION_CENTS * n_cancellations


def first_year_savings(subscriptions: list[dict]) -> float:
    """Informational projection; the payable fee is fixed per cancellation."""
    from decimal import Decimal, ROUND_HALF_UP
    total = Decimal("0")
    for subscription in subscriptions:
        amount = Decimal(str(subscription.get("savings_monthly") or 0))
        if not amount.is_finite() or amount < 0:
            raise ValueError("Savings must be finite and nonnegative.")
        total += amount * 12  # savings_monthly is normalized by the outcome API.
    return float(total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
