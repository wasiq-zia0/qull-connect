"""Authenticated MCP tools using the same validated business functions as REST."""
import json
import os
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from urllib.parse import urlsplit
from identity import IdentityMiddleware, require_owner
from limits import RateLimitMiddleware, BodySizeLimitMiddleware
import app as api

server = MCPServer("subscription-slayer")


def create_mcp_app():
    # Stateless requests bind every tool call to this request's verified user.
    base = urlsplit(os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1"))
    transport = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", base.netloc],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", f"{base.scheme}://{base.netloc}"],
    )
    mcp_app = server.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=True, transport_security=transport)
    mcp_app.add_middleware(IdentityMiddleware)
    mcp_app.add_middleware(RateLimitMiddleware)
    mcp_app.add_middleware(BodySizeLimitMiddleware)
    return mcp_app


def _call(fn, *args, **kwargs) -> dict:
    require_owner()
    try:
        result = fn(*args, **kwargs)
        if isinstance(result, JSONResponse):
            data = json.loads(result.body)
            if result.status_code >= 400:
                return {"error": data, "status_code": result.status_code}
            return data
        return result
    except HTTPException as exc:
        return {"error": exc.detail, "status_code": exc.status_code}


@server.tool()
def scan_subscriptions(source: str = "fixtures", max_results: int = 100) -> dict:
    """Run a clearly marked fixture demo. Import user-supplied receipts for real detection; Gmail OAuth is not connected."""
    return _call(api.scan, api.ScanRequest(source=source, max=max_results))

@server.tool()
def import_receipts(receipts: list[dict]) -> dict:
    """Detect recurring charges from receipts supplied by this user. No mailbox access."""
    return _call(api.import_receipts, api.ReceiptImport(receipts=receipts))

@server.tool()
def add_subscription(merchant: str, amount: float, currency: str = "USD", frequency: str = "monthly") -> dict:
    """Track a recurring charge manually; the user reviews and cancels it themselves."""
    return _call(api.add_subscription, api.ManualSubscription(merchant=merchant, amount=amount, currency=currency, frequency=frequency))

@server.tool()
def list_subscriptions(status: str | None = None) -> dict:
    """List only the authenticated user's tracked subscriptions."""
    return _call(api.list_subscriptions, status)

@server.tool()
def update_subscription(subscription_id: int, status: str | None = None, savings_monthly: float | None = None, notes: str | None = None) -> dict:
    """Record the user's cancellation status and monthly saving; does not cancel with a merchant."""
    return _call(api.update_subscription, subscription_id, api.SubscriptionUpdate(status=status, savings_monthly=savings_monthly, notes=notes))

@server.tool()
def get_cancel_pack(subscription_id: int) -> dict:
    """Get a cancellation guide; the user completes the merchant's steps."""
    return _call(api.get_cancel_pack, subscription_id)

@server.tool()
def setup_billing(name: str, accept_fee_terms: bool, email: str = "") -> dict:
    """Return a secure hosted card-save URL and the $10 per cancellation fee disclosure. No charge."""
    return _call(api.setup_billing, api.BillingSetupRequest(name=name, email=email, accept_fee_terms=accept_fee_terms))

@server.tool()
def billing_status() -> dict:
    """Verify whether the authenticated user's payment method setup is complete."""
    return _call(api.billing_status)

@server.tool()
def confirm_savings(subscription_ids: list[int], confirm_fee: bool, fee_amount_cents: int) -> dict:
    """Charge $10 per completed cancellation only after the user explicitly agrees to the displayed fee."""
    return _call(api.confirm_savings, api.SavingsConfirmRequest(subscription_ids=subscription_ids, confirm_fee=confirm_fee, fee_amount_cents=fee_amount_cents))

@server.tool()
def fee_quote(subscription_ids: list[int]) -> dict:
    """Read the exact USD fee for a batch of completed cancellations before asking for payment consent."""
    return _call(api.savings_fee_quote, api.SavingsQuoteRequest(subscription_ids=subscription_ids))


@server.tool()
def delete_my_data(consent: bool) -> dict:
    """Delete this user's operational records after explicit confirmation. Required payment records are retained separately."""
    if consent is not True:
        raise ValueError("Explicit deletion confirmation required")
    return _call(api.delete_my_data)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_mcp_app(), host="127.0.0.1", port=int(os.environ.get("MCP_PORT", "8580")))
