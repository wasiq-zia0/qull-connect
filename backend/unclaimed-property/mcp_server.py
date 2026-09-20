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

server = MCPServer("unclaimed-property")


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
def list_states() -> dict:
    """List official state portals. The user performs the search on those portals."""
    return _call(api.list_states)

@server.tool()
def start_search(full_legal_name: str, email: str, states: list[dict], prior_names: list[str] | None = None, dob: str | None = None, dob_consent: bool = False) -> dict:
    """Prepare a free state-by-state claim guide. This does not search or file with any state."""
    return _call(api.create_search, api.IntakeRequest(full_legal_name=full_legal_name, email=email, states_of_residence=states, prior_names=prior_names or [], dob=dob, dob_consent=dob_consent))

@server.tool()
def handle_life_event(event_type: str, payload: dict) -> dict:
    """Create a move-related draft for the authenticated user only."""
    return _call(api.receive_life_event, api.LifeEvent(event_type=event_type, payload=payload))

@server.tool()
def complete_search(search_id: str, full_legal_name: str, email: str | None = None) -> dict:
    """Complete the user's draft claim guide."""
    return _call(api.update_search, search_id, api.DraftUpdate(full_legal_name=full_legal_name, email=email))

@server.tool()
def get_search(search_id: str) -> dict:
    """Read the user's claim checklist and reported status."""
    return _call(api.get_search, search_id)

@server.tool()
def get_claim_pack(search_id: str, state: str) -> dict:
    """Return a claim guide and official portal. The user searches and files for free."""
    return _call(api.claim_pack, search_id, state)

@server.tool()
def update_claim_status(search_id: str, state: str, status: str) -> dict:
    """Record the status reported by the user; no state agency is contacted."""
    return _call(api.update_state_status, search_id, state, api.StatusUpdate(status=status))

@server.tool()
def setup_billing(search_id: str, accept_fee_terms: bool) -> dict:
    """Paid assistance is unavailable pending state fee and agreement review."""
    return _call(api.billing_setup, search_id, api.BillingConsent(accept_fee_terms=accept_fee_terms))

@server.tool()
def confirm_recovery(search_id: str, amount: float, state: str, confirm_fee: bool, fee_amount_cents: int) -> dict:
    """Paid assistance remains blocked until state-specific fee eligibility is implemented."""
    return _call(api.recovery_confirmed, search_id, api.RecoveryConfirmed(amount=amount, state_abbr=state, confirm_fee=confirm_fee, fee_amount_cents=fee_amount_cents))

@server.tool()
def billing_status(search_id: str) -> dict:
    """Read the billing status for the authenticated user's search."""
    return _call(api.billing_status, search_id)

@server.tool()
def delete_my_data(consent: bool) -> dict:
    """Delete this user's operational records after explicit confirmation. Required payment records are retained separately."""
    if consent is not True:
        raise ValueError("Explicit deletion confirmation required")
    return _call(api.delete_my_data)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_mcp_app(), host="127.0.0.1", port=int(os.environ.get("MCP_PORT", "8580")))
