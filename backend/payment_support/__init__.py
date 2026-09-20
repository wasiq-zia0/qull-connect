"""Shared Stripe billing with hosted collection and a durable charge ledger.

Only a verified ``succeeded`` PaymentIntent is a successful payment. This module
never invokes a developer CLI, manufactures payments, or infers payment success
from a browser redirect. Caller identity and record ownership remain the API's
responsibility; all Stripe identifiers passed here must come from server storage.
"""
from .service import BillingClient, percent_cents

__all__ = ["BillingClient", "percent_cents"]
from .routes import install_payment_routes

__all__.append("install_payment_routes")
