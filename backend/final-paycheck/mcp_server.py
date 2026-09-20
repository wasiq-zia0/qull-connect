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
rest.db.init_db()
from identity import IdentityMiddleware, require_owner
from limits import RateLimitMiddleware, BodySizeLimitMiddleware

server = MCPServer("final-paycheck", instructions="All private operations require the caller's per-user bearer key. Present fee terms and exact amount, then collect explicit user approval before a billing mutation. Prepared documents are sent or filed by the user.")


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
    uvicorn.run(create_mcp_app(), host=os.environ.get("MCP_HOST", "127.0.0.1"), port=int(os.environ.get("MCP_PORT", "8575")), log_level="warning")

@server.tool()
def get_state_law(state: str) -> dict:
    return _call(rest.state_law, state)

@server.tool()
def create_case(employee_name: str, state: str, last_day_worked: str,
                termination_type: str, wages_owed: float, employer_name: str,
                employee_email: str = "", employer_address: str = "", pay_period: str = "",
                next_payday: str | None = None, forwarding_address: str = "") -> dict:
    return _call(rest.create_case, rest.Intake(**locals()))

@server.tool()
def confirm_case(case_id: str, fields: dict) -> dict:
    """Complete missing draft intake before activating the case."""
    return _call(rest.confirm_case, case_id, rest.ConfirmDraft(**fields))

@server.tool()
def get_case_status(case_id: str) -> dict:
    return _call(rest.get_case, case_id)

@server.tool()
def generate_demand_letter(case_id: str) -> dict:
    """Generate factual wage-request PDF bytes for the user to review and send."""
    return _call(rest.demand_letter, case_id)

@server.tool()
def setup_billing(case_id: str, accept_fee_terms: bool) -> dict:
    return _call(rest.billing_setup, case_id, rest.BillingConsentIn(accept_fee_terms=accept_fee_terms))

@server.tool()
def billing_status(case_id: str) -> dict:
    return _call(rest.billing_status, case_id)

@server.tool()
def confirm_recovery(case_id: str, amount: float, confirm_fee: bool, fee_amount_cents: int) -> dict:
    return _call(rest.recovery_confirmed, case_id, rest.RecoveryConfirmed(amount=amount, confirm_fee=confirm_fee, fee_amount_cents=fee_amount_cents))

@server.tool()
def receive_life_event(event_type: str, payload: dict) -> dict:
    return _call(rest.life_events, rest.LifeEvent(event_type=event_type, payload=payload))


@server.tool()
def export_my_data() -> dict:
    """Export the caller's local service records."""
    return _call(rest.export_my_data)

@server.tool()
def delete_my_data(confirm_delete: bool) -> dict:
    """Delete the caller's local cases and generated documents after explicit confirmation. Financial records may be retained by Stripe and the payment ledger."""
    return _call(rest.delete_my_data, rest.DeleteDataIn(confirm_delete=confirm_delete))


@server.tool()
def quote_fee(case_id: str, amount: float) -> dict:
    """Calculate the exact fee without charging, for the user to review."""
    return _call(rest.quote_fee, case_id, rest.FeeQuoteIn(amount=amount))


if __name__ == "__main__":
    main()
