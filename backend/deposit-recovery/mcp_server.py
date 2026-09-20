"""MCP tools use the same validated, owner-scoped operations as the REST API."""
import base64
import json
import os
from pathlib import Path
from fastapi import HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from urllib.parse import urlsplit
import app as rest
from src.identity import IdentityMiddleware, require_owner
from src.limits import RateLimitMiddleware, BodySizeLimitMiddleware

server = MCPServer("deposit-recovery", instructions="All private operations require the caller's per-user bearer key. Present fee terms and exact amount, then collect explicit user approval before a billing mutation. Prepared documents are sent or filed by the user.")


def _call(function, *args, **kwargs):
    require_owner()
    try:
        result = function(*args, **kwargs)
        if isinstance(result, FileResponse):
            path = Path(result.path)
            return {"filename": result.filename or path.name, "pdf_base64": base64.b64encode(path.read_bytes()).decode(), "user_message": "Your document is ready. Review it and send it yourself."}
        if isinstance(result, JSONResponse):
            return {"http_status": result.status_code, **json.loads(result.body)}
        return result
    except HTTPException as exc:
        return {"error": exc.detail, "http_status": exc.status_code, "user_message": str(exc.detail)}
    except (ValidationError, ValueError, KeyError) as exc:
        return {"error": str(exc), "http_status": 422, "user_message": "Check the supplied fields and try again."}


def create_mcp_app():
    # Stateless transport prevents one customer's MCP session being reused by another.
    public = urlsplit(os.environ.get("PUBLIC_BASE_URL", ""))
    hosts = ["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*"]
    origins = ["http://127.0.0.1:*", "http://localhost:*"]
    if public.scheme == "https" and public.hostname and not public.username and not public.password:
        hosts.append(public.netloc)
        origins.append("https://" + public.netloc)
    application = server.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=origins))
    application.add_middleware(IdentityMiddleware)
    application.add_middleware(RateLimitMiddleware)
    application.add_middleware(BodySizeLimitMiddleware)
    return application


def main():
    import uvicorn
    uvicorn.run(create_mcp_app(), host=os.environ.get("MCP_HOST", "127.0.0.1"), port=int(os.environ.get("MCP_PORT", "8571")), log_level="warning")

@server.tool()
def create_case(tenant_name: str, tenant_forwarding_address: str, state: str, move_out: str,
                deposit: float, landlord_name: str, landlord_address: str, rental_address: str,
                forwarding_date: str | None = None, tenancy_end: str | None = None) -> dict:
    """Save deposit facts. Reviewed deadlines have limited scope; other states need review."""
    return _call(rest.api_create_case, rest.CaseIn(**locals()))

@server.tool()
def get_case_status(case_id: str) -> dict:
    return _call(rest.api_get_case, case_id)

@server.tool()
def generate_demand_letter(case_id: str) -> dict:
    """Prepare a factual request; the user reviews and sends it."""
    return _call(rest.api_demand_letter, case_id)

@server.tool()
def mark_letter_status(case_id: str, status: str) -> dict:
    return _call(rest.api_letter_status, case_id, rest.LetterStatusIn(status=status))

@server.tool()
def get_state_law(state: str) -> dict:
    return _call(rest.api_get_state, state)

@server.tool()
def setup_billing(case_id: str, accept_fee_terms: bool) -> dict:
    """Open secure card setup only after the user accepts the disclosed 25% fee terms."""
    return _call(rest.api_billing_setup, case_id, rest.BillingConsentIn(accept_fee_terms=accept_fee_terms))

@server.tool()
def billing_status(case_id: str) -> dict:
    return _call(rest.api_billing_status, case_id)

@server.tool()
def confirm_recovery(case_id: str, amount_recovered: float, confirm_fee: bool, fee_amount_cents: int) -> dict:
    """Charge only the exact fee the user explicitly approved on their received recovery."""
    return _call(rest.api_recovery_confirmed, case_id, rest.RecoveryIn(amount_recovered=amount_recovered, confirm_fee=confirm_fee, fee_amount_cents=fee_amount_cents))

@server.tool()
def receive_life_event(event_type: str, payload: dict) -> dict:
    return _call(rest.api_life_events, rest.LifeEventIn(event_type=event_type, payload=payload))

@server.tool()
def get_draft(draft_id: str) -> dict:
    return _call(rest.api_get_draft, draft_id)

@server.tool()
def promote_draft(draft_id: str, fields: dict) -> dict:
    return _call(rest.api_promote_draft, draft_id, rest.PromoteIn(**fields))


@server.tool()
def export_my_data() -> dict:
    """Export the caller's local service records."""
    return _call(rest.export_my_data)

@server.tool()
def delete_my_data(confirm_delete: bool) -> dict:
    """Delete the caller's local cases and generated documents after explicit confirmation. Financial records may be retained by Stripe and the payment ledger."""
    return _call(rest.delete_my_data, rest.DeleteDataIn(confirm_delete=confirm_delete))


@server.tool()
def quote_fee(cid: str, amount_recovered: float) -> dict:
    """Calculate the exact fee without charging, for the user to review."""
    return _call(rest.quote_fee, cid, rest.FeeQuoteIn(amount_recovered=amount_recovered))


if __name__ == "__main__":
    main()
