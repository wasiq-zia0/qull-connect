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
from identity import IdentityMiddleware, require_owner
from limits import RateLimitMiddleware, BodySizeLimitMiddleware

server = MCPServer("class-action-cash", instructions="All private operations require the caller's per-user bearer key. Present fee terms and exact amount, then collect explicit user approval before a billing mutation. Prepared documents are sent or filed by the user.")


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
    uvicorn.run(create_mcp_app(), host=os.environ.get("MCP_HOST", "127.0.0.1"), port=int(os.environ.get("MCP_PORT", "8576")), log_level="warning")

@server.tool()
def list_settlements() -> dict:
    """List source-verified unexpired settlements; matches are only eligibility candidates."""
    return _call(rest.settlements)

@server.tool()
def scan_receipts(receipts: list[dict], source: str = "receipts") -> dict:
    """Match user-supplied receipt excerpts. Gmail OAuth is not implemented."""
    return _call(rest.scan, rest.ScanRequest(source=source, receipts=receipts))

@server.tool()
def get_matches() -> dict:
    return _call(rest.matches)

@server.tool()
def generate_claim_pack(match_id: str, eligibility_confirmed: bool, full_name: str = "", email: str = "", address: str = "") -> dict:
    """Prepare filing information after the user reviews and confirms class eligibility."""
    return _call(rest.claim_pack, match_id, rest.ClaimPackRequest(eligibility_confirmed=eligibility_confirmed, full_name=full_name, email=email, address=address))

@server.tool()
def setup_billing(match_id: str, name: str, accept_fee_terms: bool, email: str = "") -> dict:
    return _call(rest.billing_setup, match_id, rest.BillingSetupRequest(name=name, email=email, accept_fee_terms=accept_fee_terms))

@server.tool()
def billing_status(match_id: str) -> dict:
    return _call(rest.billing_status, match_id)

@server.tool()
def confirm_payout(match_id: str, amount: float, confirm_fee: bool, fee_amount_cents: int) -> dict:
    return _call(rest.payout_confirmed, match_id, rest.PayoutConfirmRequest(amount=amount, confirm_fee=confirm_fee, fee_amount_cents=fee_amount_cents))

@server.tool()
def receive_life_event(event_type: str, payload: dict) -> dict:
    return _call(rest.life_events, rest.LifeEventRequest(event_type=event_type, payload=payload))


@server.tool()
def export_my_data() -> dict:
    """Export the caller's local service records."""
    return _call(rest.export_my_data)

@server.tool()
def delete_my_data(confirm_delete: bool) -> dict:
    """Delete the caller's local cases and generated documents after explicit confirmation. Financial records may be retained by Stripe and the payment ledger."""
    return _call(rest.delete_my_data, rest.DeleteDataIn(confirm_delete=confirm_delete))


@server.tool()
def quote_fee(match_id: str, amount: float) -> dict:
    """Calculate the exact fee without charging, for the user to review."""
    return _call(rest.quote_fee, match_id, rest.FeeQuoteIn(amount=amount))


if __name__ == "__main__":
    main()
