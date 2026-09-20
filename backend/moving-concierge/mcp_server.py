"""Authenticated MCP tools using the same validated business functions as REST."""
import json
import os
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from urllib.parse import urlsplit
from src.identity import IdentityMiddleware, require_owner
from src.limits import RateLimitMiddleware, BodySizeLimitMiddleware
import app as api

server = MCPServer("moving-concierge")


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
def create_move(name: str, email: str, old_address: str, new_address: str, move_date: str, state: str, draft_id: str | None = None) -> dict:
    """Prepare a move checklist; the user completes all address changes themselves. $49 one-time unlock."""
    return _call(api.create_move, api.MoveIn(name=name, email=email, old_address=old_address, new_address=new_address, move_date=move_date, state=state, draft_id=draft_id))

@server.tool()
def get_move(move_id: str) -> dict:
    """Read the user's move status. Pack details require payment."""
    return _call(api.get_move, move_id)

@server.tool()
def get_pack(move_id: str) -> dict:
    """Return the paid checklist in Markdown and its authenticated PDF endpoint."""
    return _call(api.get_pack, move_id, "md")

@server.tool()
def update_checklist(move_id: str, items: list[dict]) -> dict:
    """Record completed or inapplicable steps on the user's paid checklist."""
    return _call(api.update_checklist, move_id, api.ChecklistPatch(items=items))

@server.tool()
def setup_billing(move_id: str, accept_fee_terms: bool) -> dict:
    """Return the secure card-save URL and disclose the one-time $49 fee. No charge."""
    return _call(api.billing_setup, move_id, api.BillingConsent(accept_fee_terms=accept_fee_terms))

@server.tool()
def pay(move_id: str, confirm_fee: bool, fee_amount_cents: int) -> dict:
    """Charge $49 and unlock this move only after the user explicitly consents to the fee."""
    return _call(api.pay, move_id, api.ChargeConsent(confirm_fee=confirm_fee, fee_amount_cents=fee_amount_cents))

@server.tool()
def billing_status(move_id: str) -> dict:
    """Verify saved-card state and paid status."""
    return _call(api.billing_status, move_id)

@server.tool()
def receive_life_event(event_type: str, payload: dict) -> dict:
    """Create a move draft bound to the authenticated user."""
    return _call(api.life_event, api.LifeEventIn(event_type=event_type, payload=payload))

@server.tool()
def delete_my_data(consent: bool) -> dict:
    """Delete this user's operational records after explicit confirmation. Required payment records are retained separately."""
    if consent is not True:
        raise ValueError("Explicit deletion confirmation required")
    return _call(api.delete_my_data)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_mcp_app(), host="127.0.0.1", port=int(os.environ.get("MCP_PORT", "8580")))
