"""Stripe transport and durable idempotency, shared by the ten connectors."""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import string
import time
from urllib.parse import urlsplit

import stripe
from stripe import StripeClient

API_VERSION = "2026-07-29.dahlia"
# Stripe retains idempotency keys for at least 24 hours. An unresolved create
# older than this smaller window must be reconciled, never silently recreated.
RETRY_WINDOW_SECONDS = 23 * 60 * 60


class BillingError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def percent_cents(amount: float | str | Decimal, rate: float | str | Decimal) -> int:
    """Exact decimal fees; reject non-finite/negative inputs, round half up."""
    try:
        value, fraction = Decimal(str(amount)), Decimal(str(rate))
        if not value.is_finite() or not fraction.is_finite() or value < 0 or not 0 <= fraction <= 1:
            raise ValueError("Amounts must be finite and nonnegative; rates must be between zero and one.")
        return int((value * fraction * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("Invalid monetary amount.") from exc


def _id(value):
    return value.get("id") if isinstance(value, dict) else value


def _data(value):
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "to_dict_recursive"):
        return value.to_dict_recursive()
    return dict(value)


def _needs_authentication(intent):
    error = intent.get("last_payment_error") or {}
    return intent.get("status") == "requires_action" or (
        intent.get("status") == "requires_payment_method"
        and "authentication_required" in {error.get("code"), error.get("decline_code")}
    )


def _failure(code, message, **extra):
    return {"error": message, "code": code, **extra}


def _safe_error(exc):
    if isinstance(exc, BillingError):
        return _failure(exc.code, str(exc))
    if isinstance(exc, stripe.AuthenticationError):
        return _failure("billing_configuration", "Billing credentials are unavailable. Contact support.")
    if isinstance(exc, stripe.PermissionError):
        return _failure("billing_configuration", "Billing permissions need attention. Contact support.")
    if isinstance(exc, stripe.APIConnectionError):
        return _failure("billing_unavailable", "Payment provider did not respond. Retry this same request; do not create a new case.")
    if isinstance(exc, stripe.RateLimitError):
        return _failure("billing_busy", "Payment provider is busy. Retry this same request shortly.")
    if isinstance(exc, stripe.StripeError):
        return _failure("billing_failed", "Payment provider could not complete this request. No payment is confirmed; contact support if this continues.")
    # Never leak a Stripe response, key, card detail, local path or exception text.
    return _failure("billing_unavailable", "Billing is temporarily unavailable. No payment is confirmed; retry this same request.")


class BillingClient:
    def __init__(self, connector: str, data_dir: Path, *, currency="usd", disclosure=""):
        self.connector = connector
        self.data_dir = Path(os.environ.get("DATA_DIR", data_dir))
        self.currency = currency
        self.disclosure = disclosure

    def _client(self):
        key = os.environ.get("STRIPE_SECRET_KEY", "")
        mode = os.environ.get("STRIPE_MODE", "test")
        prefixes = ("rk_test_", "sk_test_", "rkcs_test_") if mode == "test" else ("rk_live_", "sk_live_")
        if mode not in {"test", "live"} or not key.startswith(prefixes):
            raise BillingError("billing_configuration", "Billing is not configured for this environment. Contact support.")
        return StripeClient(key, stripe_version=API_VERSION, max_network_retries=2)

    def _base_url(self):
        base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
        parsed = urlsplit(base)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise BillingError("billing_configuration", "Billing return URL is not configured. Contact support.")
        return base

    @contextmanager
    def _ledger(self):
        path = Path(os.environ.get("BILLING_LEDGER_PATH", self.data_dir / "billing-ledger.db"))
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=60)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""CREATE TABLE IF NOT EXISTS payment_operations (
            operation_key TEXT PRIMARY KEY, request_hash TEXT NOT NULL,
            remote_id TEXT, created_at REAL NOT NULL, generation INTEGER NOT NULL DEFAULT 0,
            consent_at REAL, last_status TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS payment_auth_tokens (
            token_hash TEXT PRIMARY KEY, operation_key TEXT NOT NULL,
            customer_id TEXT NOT NULL, payment_intent_id TEXT NOT NULL,
            expires_at REAL NOT NULL, used_at REAL)""")
        conn.commit()
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _operation(self, conn, kind, key, payload, *, consent=False):
        if not isinstance(key, str) or not key.strip() or len(key) > 1000:
            raise BillingError("idempotency_required", "A stable billing request identifier is required.")
        token = hashlib.sha256((self.connector + ":" + kind + ":" + key).encode()).hexdigest()
        signature = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        # Commit the reservation BEFORE the network call. A crash must not erase
        # the first-attempt time and enable a duplicate after Stripe's retention.
        conn.execute("INSERT OR IGNORE INTO payment_operations(operation_key,request_hash,created_at,consent_at) VALUES(?,?,?,?)",
                     (token, signature, time.time(), time.time() if consent else None))
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        row = dict(conn.execute("SELECT * FROM payment_operations WHERE operation_key=?", (token,)).fetchone())
        if row["request_hash"] != signature:
            raise BillingError("billing_conflict", "This billing request already has a different customer or amount. Contact support; do not start another charge.")
        if kind == "fee" and not row["remote_id"] and row["last_status"] == "creating" and time.time() - row["created_at"] >= RETRY_WINDOW_SECONDS:
            raise BillingError("billing_reconciliation_required", "An earlier billing request needs provider reconciliation before retrying. Contact support.")
        row["stripe_key"] = f"qull-{token}-{row['generation']}"
        return row

    @staticmethod
    def _mark_started(conn, row):
        if not row["remote_id"] and row["last_status"] != "creating":
            conn.execute("UPDATE payment_operations SET created_at=?,last_status='creating' WHERE operation_key=?", (time.time(), row["operation_key"]))
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")

    @staticmethod
    def _record(conn, row, obj):
        remote_id = obj.get("id")
        if not remote_id:
            raise BillingError("billing_failed", "Payment provider returned an incomplete response.")
        conn.execute("UPDATE payment_operations SET remote_id=?,last_status=? WHERE operation_key=?",
                     (remote_id, obj.get("status"), row["operation_key"]))

    def setup_customer(self, name, email="", idempotency_key=None):
        try:
            client = self._client()
            payload = {"name": name, "email": email}
            with self._ledger() as conn:
                row = self._operation(conn, "customer", idempotency_key, payload)
                if row["remote_id"]:
                    return {"customer_id": row["remote_id"]}
                params = {"name": name, "metadata": {"connector": self.connector}}
                if email:
                    params["email"] = email
                self._mark_started(conn, row)
                customer = _data(client.v1.customers.create(params, options={"idempotency_key": row["stripe_key"]}))
                self._record(conn, row, customer)
                return {"customer_id": customer["id"]}
        except Exception as exc:
            return _safe_error(exc)

    def create_card_setup(self, customer_id, idempotency_key=None):
        """Create/reuse hosted Checkout in setup mode; never charge here."""
        try:
            client, base = self._client(), self._base_url()
            with self._ledger() as conn:
                row = self._operation(conn, "setup", idempotency_key, {"customer": customer_id, "currency": self.currency})
                if row["remote_id"]:
                    session = _data(client.v1.checkout.sessions.retrieve(row["remote_id"]))
                    if _id(session.get("customer")) != customer_id or session.get("mode") != "setup":
                        raise BillingError("billing_mismatch", "Saved billing setup could not be verified.")
                    if session.get("status") in {"expired", "complete"}:
                        # A closed setup session has no usable collection URL.
                        # A new setup permits replacing a declined method, never
                        # charges money, and uses a persisted generation key.
                        conn.execute("UPDATE payment_operations SET remote_id=NULL,generation=generation+1,last_status=NULL,created_at=? WHERE operation_key=?", (time.time(), row["operation_key"]))
                        conn.commit()
                        row = self._operation(conn, "setup", idempotency_key, {"customer": customer_id, "currency": self.currency})
                    else:
                        return self._setup_result(session)
                params = {
                    "mode": "setup", "customer": customer_id, "currency": self.currency,
                    "success_url": base + "/billing/return?result=success",
                    "cancel_url": base + "/billing/return?result=cancel",
                    "metadata": {"connector": self.connector},
                    "setup_intent_data": {"metadata": {"connector": self.connector}},
                    "custom_text": {"submit": {"message": self.disclosure or "Save a payment method for a later fee that you explicitly confirm. Saving does not charge you."}},
                    # Random suffix is deterministic per persisted operation so
                    # retried creates send byte-identical parameters.
                    "integration_identifier": "qull-" + self.connector + "-" + "".join(string.ascii_lowercase[int(c, 16)] for c in row["operation_key"][:8]),
                }
                self._mark_started(conn, row)
                session = _data(client.v1.checkout.sessions.create(params, options={"idempotency_key": row["stripe_key"]}))
                self._record(conn, row, session)
                return self._setup_result(session)
        except Exception as exc:
            return _safe_error(exc)

    @staticmethod
    def _setup_result(session):
        return {"checkout_session_id": session["id"], "setup_url": session.get("url"),
                "setup_intent_id": _id(session.get("setup_intent")), "client_secret": None,
                "status": session.get("status", "open"), "charged": False}

    def retrieve_card_setup(self, customer_id, setup_intent_id=None, checkout_session_id=None):
        try:
            return self._verified_setup(self._client(), customer_id, setup_intent_id, checkout_session_id)
        except Exception as exc:
            return _safe_error(exc)

    def _verified_setup(self, client, customer_id, setup_intent_id=None, checkout_session_id=None):
        if checkout_session_id:
            session = _data(client.v1.checkout.sessions.retrieve(checkout_session_id))
            if _id(session.get("customer")) != customer_id or session.get("mode") != "setup":
                raise BillingError("billing_mismatch", "The saved checkout belongs to a different billing record.")
            if session.get("status") != "complete":
                return _failure("setup_incomplete", "Open the secure setup link and finish saving your payment method first.",
                                status=session.get("status"), setup_url=session.get("url"), checkout_session_id=checkout_session_id)
            session_si = _id(session.get("setup_intent"))
            if setup_intent_id and setup_intent_id != session_si:
                raise BillingError("billing_mismatch", "The saved payment setup could not be verified.")
            setup_intent_id = session_si
        if not setup_intent_id or not customer_id:
            return _failure("setup_required", "Save a payment method using the secure setup link first.")
        setup = _data(client.v1.setup_intents.retrieve(setup_intent_id))
        if _id(setup.get("customer")) != customer_id:
            raise BillingError("billing_mismatch", "The saved payment setup belongs to a different customer.")
        if setup.get("status") != "succeeded" or setup.get("usage") != "off_session":
            return _failure("setup_incomplete", "The payment method has not been saved for later use.", status=setup.get("status"))
        payment_method_id = _id(setup.get("payment_method"))
        if not payment_method_id:
            return _failure("setup_incomplete", "No saved payment method is available.")
        method = _data(client.v1.payment_methods.retrieve(payment_method_id))
        if _id(method.get("customer")) != customer_id:
            raise BillingError("billing_mismatch", "The payment method is no longer attached to this customer.")
        return {"status": "succeeded", "card_saved": True, "setup_intent_id": setup_intent_id,
                "checkout_session_id": checkout_session_id, "payment_method_id": payment_method_id}

    def charge_fee(self, customer_id, amount_cents, description, idempotency_key=None, *,
                   setup_intent_id=None, checkout_session_id=None, consent=False):
        try:
            if consent is not True:
                return _failure("consent_required", "Confirm the displayed fee before paying.")
            if isinstance(amount_cents, bool) or not isinstance(amount_cents, int) or not 0 < amount_cents <= 99_999_999:
                return _failure("invalid_fee", "The fee must be a positive integer amount in the smallest currency unit.")
            client = self._client()
            self._base_url()
            request = {"customer": customer_id, "amount": amount_cents, "currency": self.currency}
            with self._ledger() as conn:
                row = self._operation(conn, "fee", idempotency_key, request, consent=True)
                if row["remote_id"]:
                    intent = _data(client.v1.payment_intents.retrieve(row["remote_id"]))
                    if _id(intent.get("customer")) != customer_id or intent.get("amount") != amount_cents or intent.get("currency") != self.currency:
                        raise BillingError("billing_mismatch", "The saved payment does not match this fee. Contact support.")
                    # A pending/authentication-required charge must continue on
                    # the same intent. Never create a second payment for it.
                    if _needs_authentication(intent) or intent.get("status") not in {"requires_payment_method", "requires_confirmation"}:
                        self._record(conn, row, intent)
                        return self._payment_result(intent, conn, row)
                verified = self._verified_setup(client, customer_id, setup_intent_id, checkout_session_id)
                if verified.get("error"):
                    return verified
                params = {"payment_method": verified["payment_method_id"], "off_session": True}
                try:
                    if row["remote_id"]:
                        # A newly saved method can retry a declined charge without
                        # creating a second PaymentIntent. Same method retries
                        # replay the original confirmation safely.
                        retry_key = row["stripe_key"] + "-confirm-" + hashlib.sha256(verified["payment_method_id"].encode()).hexdigest()[:16]
                        intent = _data(client.v1.payment_intents.confirm(row["remote_id"], params, options={"idempotency_key": retry_key}))
                    else:
                        params.update(request)
                        params.update({"confirm": True, "description": description,
                                       "metadata": {"connector": self.connector, "billing_operation": row["operation_key"]},
                                       "automatic_payment_methods": {"enabled": True, "allow_redirects": "never"}})
                        self._mark_started(conn, row)
                        intent = _data(client.v1.payment_intents.create(params, options={"idempotency_key": row["stripe_key"]}))
                except stripe.CardError as exc:
                    error = getattr(exc, "error", None)
                    pending = getattr(error, "payment_intent", None)
                    if not pending:
                        return _failure("payment_declined", "The payment method was declined. Save a different method and retry this same fee.")
                    intent = _data(client.v1.payment_intents.retrieve(pending)) if isinstance(pending, str) else _data(pending)
                self._record(conn, row, intent)
                return self._payment_result(intent, conn, row)
        except Exception as exc:
            return _safe_error(exc)

    def _payment_result(self, intent, conn, row):
        common = {"payment_intent_id": intent["id"], "status": intent.get("status"),
                  "amount_cents": intent.get("amount"), "currency": intent.get("currency", self.currency)}
        status = intent.get("status")
        if status == "succeeded":
            return common
        if _needs_authentication(intent):
            token = secrets.token_urlsafe(32)
            conn.execute("DELETE FROM payment_auth_tokens WHERE expires_at < ?", (time.time(),))
            conn.execute("INSERT INTO payment_auth_tokens(token_hash,operation_key,customer_id,payment_intent_id,expires_at) VALUES(?,?,?,?,?)",
                         (hashlib.sha256(token.encode()).hexdigest(), row["operation_key"], _id(intent.get("customer")), intent["id"], time.time() + 900))
            return _failure("payment_action_required", "Your bank requires authentication. Open the secure authorization link, then retry this same confirmation.",
                            authorization_url=self._base_url() + "/billing/authenticate#" + token, **common)
        if status == "processing":
            return _failure("payment_processing", "Payment is processing. Retry this same confirmation later; no second payment will be created.", **common)
        if status == "requires_capture":
            return _failure("payment_not_captured", "Payment was authorized but is not captured. Contact support; it is not yet paid.", **common)
        return _failure("payment_not_completed", "Payment was not completed. Save a valid payment method and retry this same fee.", **common)


    def exchange_auth_token(self, token):
        """Consume a capability once; disclose only its bound PaymentIntent."""
        if not isinstance(token, str) or len(token) != 43:
            return _failure("invalid_authorization", "This authorization link is invalid or expired. Request a new link from your connector.")
        try:
            client = self._client()
            mode = os.environ.get("STRIPE_MODE", "test")
            publishable = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
            if not publishable.startswith("pk_" + mode + "_"):
                raise BillingError("billing_configuration", "Payment authentication is not configured. Contact support.")
            with self._ledger() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT t.*,o.remote_id,o.request_hash FROM payment_auth_tokens t JOIN payment_operations o ON o.operation_key=t.operation_key WHERE token_hash=?",
                                   (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
                if not row or row["used_at"] or row["expires_at"] <= time.time() or row["remote_id"] != row["payment_intent_id"]:
                    return _failure("invalid_authorization", "This authorization link is invalid or expired. Request a new link from your connector.")
                intent = _data(client.v1.payment_intents.retrieve(row["payment_intent_id"]))
                actual = {"customer": _id(intent.get("customer")), "amount": intent.get("amount"), "currency": intent.get("currency")}
                signature = hashlib.sha256(json.dumps(actual, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                if actual["customer"] != row["customer_id"] or signature != row["request_hash"]:
                    raise BillingError("billing_mismatch", "The payment authorization could not be verified.")
                conn.execute("UPDATE payment_auth_tokens SET used_at=? WHERE token_hash=?", (time.time(), hashlib.sha256(token.encode()).hexdigest()))
                if intent.get("status") == "succeeded":
                    return {"status": "succeeded"}
                if not _needs_authentication(intent) or not intent.get("client_secret"):
                    return _failure("payment_not_pending", "There is no payment authentication pending. Return to the connector to check payment status.")
                return {"status": "requires_action", "client_secret": intent["client_secret"], "publishable_key": publishable,
                        "action": "confirm_payment" if intent.get("status") == "requires_payment_method" else "handle_next_action",
                        "return_url": self._base_url() + "/billing/return"}
        except Exception as exc:
            return _safe_error(exc)

    def reconcile_payment(self, operation_key, payment_intent_id, *, apply=False):
        """Operator-only recovery after a crash; reads Stripe and never charges.

        The supplied intent must carry this exact ledger operation's metadata
        and match its immutable customer, amount, and currency fingerprint.
        Only apply=True links it. This function is never exposed as an API.
        """
        try:
            client = self._client()
            with self._ledger() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM payment_operations WHERE operation_key=?", (operation_key,)).fetchone()
                if not row or (row["remote_id"] and row["remote_id"] != payment_intent_id):
                    raise BillingError("billing_mismatch", "This ledger operation cannot be linked to that payment.")
                intent = _data(client.v1.payment_intents.retrieve(payment_intent_id))
                metadata = intent.get("metadata") or {}
                actual = {"customer": _id(intent.get("customer")), "amount": intent.get("amount"), "currency": intent.get("currency")}
                signature = hashlib.sha256(json.dumps(actual, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                if metadata.get("connector") != self.connector or metadata.get("billing_operation") != operation_key or signature != row["request_hash"]:
                    raise BillingError("billing_mismatch", "Provider metadata, customer, amount or currency does not match this ledger operation.")
                if apply:
                    self._record(conn, row, intent)
                return {"verified": True, "applied": apply, "payment_intent_id": intent["id"],
                        "status": intent.get("status"), "amount_cents": intent.get("amount"), "currency": intent.get("currency")}
        except Exception as exc:
            return _safe_error(exc)
