"""Provider-isolated billing tests. No API keys or real Stripe requests required."""
from copy import deepcopy
from pathlib import Path
import hashlib
import sqlite3
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from payment_support import BillingClient, percent_cents, install_payment_routes
import payment_support.service as module


@pytest.fixture
def billing(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.invalid/deposit-recovery")
    monkeypatch.setenv("STRIPE_MODE", "test")
    monkeypatch.setenv("STRIPE_PUBLISHABLE_KEY", "pk_test_mock")
    monkeypatch.setenv("BILLING_LEDGER_PATH", str(tmp_path / "ledger.db"))
    service = BillingClient("deposit-recovery", tmp_path)
    setup = {"id": "seti_owner", "customer": "cus_owner", "status": "succeeded", "usage": "off_session", "payment_method": "pm_owner"}
    session = {"id": "cs_owner", "customer": "cus_owner", "mode": "setup", "status": "complete", "setup_intent": "seti_owner", "url": None}
    intent = {"id": "pi_owner", "customer": "cus_owner", "status": "succeeded", "amount": 2500, "currency": "usd"}
    api = SimpleNamespace(
        customers=SimpleNamespace(create=Mock(return_value={"id": "cus_owner"})),
        checkout=SimpleNamespace(sessions=SimpleNamespace(create=Mock(return_value={**session, "status": "open", "setup_intent": None, "url": "https://checkout.stripe.com/example"}),retrieve=Mock(return_value=session))),
        setup_intents=SimpleNamespace(retrieve=Mock(return_value=setup)),
        payment_methods=SimpleNamespace(retrieve=Mock(return_value={"id": "pm_owner", "customer": "cus_owner"})),
        payment_intents=SimpleNamespace(create=Mock(return_value=intent), retrieve=Mock(return_value=intent), confirm=Mock(return_value=intent)),
    )
    client = SimpleNamespace(v1=api)
    monkeypatch.setattr(service, "_client", lambda: client)
    return service, api, tmp_path / "ledger.db"


def pay(service, **overrides):
    args = dict(customer_id="cus_owner", amount_cents=2500, description="Agreed fee", idempotency_key="user-case-fee", checkout_session_id="cs_owner", consent=True)
    args.update(overrides)
    return service.charge_fee(**args)


def test_off_session_uses_verified_method_and_true_success(billing):
    service, api, _ = billing
    result = pay(service)
    assert result["status"] == "succeeded" and not result.get("error")
    params, = api.payment_intents.create.call_args.args
    assert params["payment_method"] == "pm_owner"
    assert params["off_session"] is True and params["confirm"] is True
    assert params["currency"] == "usd"
    assert "payment_method_types" not in params
    assert "idempotency_key" in api.payment_intents.create.call_args.kwargs["options"]


def test_replay_after_stripe_retention_retrieves_same_payment(billing):
    service, api, path = billing
    first = pay(service)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE payment_operations SET created_at=?", (time.time()-7*86400,))
    second = pay(service)
    assert first == second
    assert api.payment_intents.create.call_count == 1
    assert api.payment_intents.retrieve.call_count == 1


def test_changed_fee_same_operation_is_rejected(billing):
    service, api, _ = billing
    pay(service)
    assert pay(service, amount_cents=2600)["code"] == "billing_conflict"
    assert api.payment_intents.create.call_count == 1


def test_unknown_create_older_than_retention_fails_closed(billing):
    service, api, path = billing
    api.payment_intents.create.side_effect = module.stripe.APIConnectionError("mock timeout")
    assert pay(service)["code"] == "billing_unavailable"
    with sqlite3.connect(path) as db:
        db.execute("UPDATE payment_operations SET created_at=?", (time.time()-86400,))
    assert pay(service)["code"] == "billing_reconciliation_required"
    assert api.payment_intents.create.call_count == 1


def test_pending_setup_can_be_completed_later_without_false_reconciliation(billing):
    service, api, path = billing
    api.checkout.sessions.retrieve.return_value["status"] = "open"
    assert pay(service)["code"] == "setup_incomplete"
    with sqlite3.connect(path) as db:
        db.execute("UPDATE payment_operations SET created_at=?", (time.time()-86400,))
    api.checkout.sessions.retrieve.return_value["status"] = "complete"
    assert pay(service)["status"] == "succeeded"
    assert api.payment_intents.create.call_count == 1


@pytest.mark.parametrize("status,code", [("requires_capture","payment_not_captured"),("processing","payment_processing"),("canceled","payment_not_completed")])
def test_nonfinal_payment_never_reported_paid(billing, status, code):
    service, api, _ = billing
    api.payment_intents.create.return_value["status"] = status
    assert pay(service)["code"] == code


@pytest.mark.parametrize("target", ["checkout", "setup", "method"])
def test_rejects_cross_customer_binding(billing, target):
    service, api, _ = billing
    method = {"checkout": api.checkout.sessions.retrieve, "setup": api.setup_intents.retrieve, "method": api.payment_methods.retrieve}[target]
    method.return_value["customer"] = "cus_other_user"
    assert pay(service)["code"] == "billing_mismatch"
    api.payment_intents.create.assert_not_called()


def test_consent_required_before_provider_call(billing):
    service, api, _ = billing
    assert pay(service, consent=False)["code"] == "consent_required"
    api.checkout.sessions.retrieve.assert_not_called()
    api.payment_intents.create.assert_not_called()


@pytest.mark.parametrize("amount", [False,0,-1,1.5,100_000_000])
def test_invalid_fee_rejected(billing, amount):
    service, api, _ = billing
    assert pay(service, amount_cents=amount)["code"] == "invalid_fee"
    api.payment_intents.create.assert_not_called()


def test_hosted_setup_uses_trusted_redirects_and_never_charges(billing):
    service, api, _ = billing
    result = service.create_card_setup("cus_owner", "owner-record-setup")
    assert result["setup_url"].startswith("https://checkout.stripe.com/")
    assert result["setup_intent_id"] is None and result["charged"] is False
    params, = api.checkout.sessions.create.call_args.args
    assert params["mode"] == "setup"
    assert params["success_url"] == "https://example.invalid/deposit-recovery/billing/return?result=success"
    assert "payment_method_types" not in params
    api.payment_intents.create.assert_not_called()


def test_expired_hosted_setup_gets_new_generation(billing):
    service, api, _ = billing
    service.create_card_setup("cus_owner", "owner-record-setup")
    first_key = api.checkout.sessions.create.call_args.kwargs["options"]["idempotency_key"]
    api.checkout.sessions.retrieve.return_value["status"] = "expired"
    service.create_card_setup("cus_owner", "owner-record-setup")
    assert api.checkout.sessions.create.call_count == 2
    assert api.checkout.sessions.create.call_args.kwargs["options"]["idempotency_key"] != first_key


def test_customer_and_setup_have_distinct_provider_keys(billing):
    service, api, _ = billing
    service.setup_customer("Owner", idempotency_key="owner-record")
    service.create_card_setup("cus_owner", "owner-record")
    assert api.customers.create.call_args.kwargs["options"]["idempotency_key"] != api.checkout.sessions.create.call_args.kwargs["options"]["idempotency_key"]


def test_sca_token_is_fragment_only_single_use_and_stored_hashed(billing):
    service, api, path = billing
    api.payment_intents.create.return_value.update(status="requires_action", client_secret="pi_owner_secret_mock")
    result = pay(service)
    assert result["code"] == "payment_action_required" and "client_secret" not in result
    token = result["authorization_url"].split("#")[1]
    assert len(token) == 43
    with sqlite3.connect(path) as db:
        stored = db.execute("SELECT token_hash FROM payment_auth_tokens").fetchone()[0]
    assert stored == hashlib.sha256(token.encode()).hexdigest() and token != stored
    assert service.exchange_auth_token(token)["client_secret"] == "pi_owner_secret_mock"
    assert service.exchange_auth_token(token)["code"] == "invalid_authorization"


def test_sca_token_expiry(billing):
    service, api, path = billing
    api.payment_intents.create.return_value.update(status="requires_action",client_secret="secret")
    token = pay(service)["authorization_url"].split("#")[1]
    with sqlite3.connect(path) as db:
        db.execute("UPDATE payment_auth_tokens SET expires_at=?", (time.time()-1,))
    assert service.exchange_auth_token(token)["code"] == "invalid_authorization"


def test_sca_token_rechecks_customer_and_amount(billing):
    service, api, _ = billing
    api.payment_intents.create.return_value.update(status="requires_action",client_secret="secret")
    token = pay(service)["authorization_url"].split("#")[1]
    api.payment_intents.retrieve.return_value["customer"] = "cus_other_user"
    assert service.exchange_auth_token(token)["code"] == "billing_mismatch"


def test_completed_sca_replay_returns_paid_without_new_charge(billing):
    service, api, _ = billing
    api.payment_intents.create.return_value.update(status="requires_action",client_secret="secret")
    pay(service)
    api.payment_intents.retrieve.return_value["status"] = "succeeded"
    assert pay(service)["status"] == "succeeded"
    assert api.payment_intents.create.call_count == 1


def test_eur_amount_stays_in_eur(billing):
    service, api, _ = billing
    service.currency = "eur"
    api.payment_intents.create.return_value["currency"] = "eur"
    assert pay(service)["currency"] == "eur"
    assert api.payment_intents.create.call_args.args[0]["currency"] == "eur"


def test_no_keys_never_falls_back_to_cli(tmp_path, monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    service = BillingClient("test", tmp_path)
    assert service.setup_customer("Owner", idempotency_key="record")["code"] == "billing_configuration"


def test_environment_mode_must_match_key(tmp_path, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_not_a_real_key")
    monkeypatch.setenv("STRIPE_MODE", "test")
    assert BillingClient("test", tmp_path).setup_customer("Owner",idempotency_key="record")["code"] == "billing_configuration"


def test_sensitive_exception_is_redacted(billing):
    service, api, _ = billing
    api.payment_intents.create.side_effect = RuntimeError("secret-key-value customer-card-data")
    response = pay(service)
    assert "secret-key-value" not in str(response)
    assert "customer-card-data" not in str(response)


@pytest.mark.parametrize("amount,rate,expected", [("0.10","0.25",3),("3.01","0.35",105),("600","0.30",18000)])
def test_decimal_half_up_fees(amount,rate,expected):
    assert percent_cents(amount,rate) == expected


@pytest.mark.parametrize("amount", [float('nan'),float('inf'),-1])
def test_nonfinite_or_negative_fees_rejected(amount):
    with pytest.raises(ValueError): percent_cents(amount,"0.25")


def test_public_auth_routes_reject_unscoped_requests(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setenv("BILLING_LEDGER_PATH", str(tmp_path/'ledger.db'))
    app=FastAPI()
    install_payment_routes(app,"test",tmp_path)
    client=TestClient(app)
    page=client.get('/billing/authenticate')
    assert page.status_code == 200
    assert page.headers['referrer-policy'] == 'no-referrer'
    assert 'no-store' in page.headers['cache-control']
    assert client.post('/billing/authenticate/session',json={'customer':'cus_other','token':'x'*43}).status_code == 400
    assert client.post('/billing/authenticate/session',json={'token':'x'}).status_code == 400


def test_stripe_authentication_exception_retains_original_intent(billing):
    service, api, _ = billing
    pending = {**api.payment_intents.create.return_value, "status": "requires_action", "client_secret": "pi_owner_secret_mock"}
    api.payment_intents.create.side_effect = module.stripe.CardError(
        "Authentication required", None, "authentication_required", json_body={"error": {"code": "authentication_required", "payment_intent": pending}}
    )
    result = pay(service)
    assert result["code"] == "payment_action_required"
    assert result["payment_intent_id"] == "pi_owner"
    api.payment_intents.retrieve.return_value["status"] = "succeeded"
    assert pay(service)["status"] == "succeeded"
    assert api.payment_intents.create.call_count == 1


def test_failed_method_retries_existing_intent_after_new_setup(billing):
    service, api, _ = billing
    api.payment_intents.create.return_value["status"] = "requires_payment_method"
    assert pay(service)["code"] == "payment_not_completed"
    api.setup_intents.retrieve.return_value["payment_method"] = "pm_replacement"
    api.payment_methods.retrieve.return_value["id"] = "pm_replacement"
    api.payment_intents.confirm.return_value = {"id":"pi_owner","customer":"cus_owner","status":"succeeded","amount":2500,"currency":"usd"}
    assert pay(service)["status"] == "succeeded"
    api.payment_intents.create.assert_called_once()
    assert api.payment_intents.confirm.call_args.args[0] == "pi_owner"
    assert api.payment_intents.confirm.call_args.args[1]["payment_method"] == "pm_replacement"


def test_completed_setup_can_be_replaced_after_decline(billing):
    service, api, _ = billing
    service.create_card_setup("cus_owner", "owner-record-setup")
    first = api.checkout.sessions.create.call_args.kwargs["options"]["idempotency_key"]
    # Existing session is complete and therefore no longer displays a form.
    assert api.checkout.sessions.retrieve.return_value["status"] == "complete"
    result = service.create_card_setup("cus_owner", "owner-record-setup")
    assert result["setup_url"]
    assert api.checkout.sessions.create.call_count == 2
    assert api.checkout.sessions.create.call_args.kwargs["options"]["idempotency_key"] != first


def test_official_sandbox_temporary_key_only_accepted_in_test_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "rkcs_test_mock")
    monkeypatch.setenv("STRIPE_MODE", "test")
    factory = Mock()
    monkeypatch.setattr(module, "StripeClient", factory)
    service = BillingClient("test",tmp_path)
    assert service._client() is factory.return_value
    monkeypatch.setenv("STRIPE_MODE", "live")
    with pytest.raises(module.BillingError): service._client()


def test_reconciliation_never_creates_payment_and_checks_metadata(billing):
    service, api, path = billing
    api.payment_intents.create.side_effect = module.stripe.APIConnectionError("mock timeout")
    pay(service)
    with sqlite3.connect(path) as db:
        operation = db.execute("SELECT operation_key FROM payment_operations").fetchone()[0]
    assert service.reconcile_payment(operation, "pi_owner")["code"] == "billing_mismatch"
    api.payment_intents.retrieve.return_value["metadata"] = {"connector":"deposit-recovery", "billing_operation":operation}
    result = service.reconcile_payment(operation, "pi_owner")
    assert result["verified"] is True and result["applied"] is False
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT remote_id FROM payment_operations").fetchone()[0] is None
    assert service.reconcile_payment(operation, "pi_owner", apply=True)["applied"] is True
    assert pay(service)["status"] == "succeeded"
    assert api.payment_intents.create.call_count == 1


def test_off_session_authentication_decline_uses_on_session_confirmation(billing):
    service, api, _ = billing
    api.payment_intents.create.return_value.update(status="requires_payment_method",client_secret="pi_owner_secret_mock",last_payment_error={"code":"card_declined","decline_code":"authentication_required"})
    result = pay(service)
    assert result["code"] == "payment_action_required"
    token = result["authorization_url"].split("#")[1]
    exchanged = service.exchange_auth_token(token)
    assert exchanged["action"] == "confirm_payment"
    assert exchanged["return_url"] == "https://example.invalid/deposit-recovery/billing/return"
    # Retrying the API before visiting the link must not make another off-session attempt.
    assert pay(service)["code"] == "payment_action_required"
    api.payment_intents.confirm.assert_not_called()
    assert api.payment_intents.create.call_count == 1
