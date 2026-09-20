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

server = MCPServer("medical-bill-fighter")


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
def create_case(patient_name: str, provider_name: str, bill_date: str, billed_patient_responsibility: float, line_items: list[dict], patient_email: str | None = None, eob: dict | None = None, insurance_plan: str | None = None, is_emergency: bool = False, is_out_of_network: bool = False, facility_in_network: bool = True) -> dict:
    """Review user-supplied bill and EOB data for possible issues. Findings are flags for provider/insurer review, not confirmed errors."""
    return _call(api.create_case, api.CaseIntake(patient_name=patient_name, provider_name=provider_name, bill_date=bill_date, billed_patient_responsibility=billed_patient_responsibility, line_items=line_items, patient_email=patient_email, eob=eob, insurance_plan=insurance_plan, is_emergency=is_emergency, is_out_of_network=is_out_of_network, facility_in_network=facility_in_network))

@server.tool()
def get_case(case_id: str) -> dict:
    """Read only the authenticated user's bill review."""
    return _call(api.get_case, case_id)

@server.tool()
def pack(case_id: str, type: str) -> dict:
    """Return an authenticated PDF download endpoint after verifying this user's case and pack type. The user reviews and sends the letter."""
    api._get_case_or_404(case_id, require_owner())
    if type not in ("dispute", "itemized", "assistance", "negotiate"):
        raise ValueError("Unknown pack type")
    response = api.get_pack(case_id, type)
    return {"case_id": case_id, "download_path": f"/api/cases/{case_id}/pack?type={type}", "media_type": "application/pdf", "size_bytes": len(response.body), "authentication_required": True, "user_message": "Your letter PDF is ready at the authenticated download endpoint. Review it and send it yourself."}

@server.tool()
def list_packs() -> dict:
    """List the available letter templates; Qull does not contact providers."""
    return _call(api.list_packs)

@server.tool()
def report_outcome(case_id: str, reduction_amount: float) -> dict:
    """Record the user's actual reduction and disclose the exact fee. No charge yet."""
    return _call(api.report_outcome, case_id, api.OutcomeReport(reduction_amount=reduction_amount))

@server.tool()
def setup_billing(case_id: str, accept_fee_terms: bool) -> dict:
    """Return a secure hosted card-save URL and the 25% fee disclosure. No charge."""
    return _call(api.billing_setup, case_id, api.BillingConsent(accept_fee_terms=accept_fee_terms))

@server.tool()
def billing_status(case_id: str) -> dict:
    """Verify payment method setup and fee status."""
    return _call(api.billing_status, case_id)

@server.tool()
def confirm_reduction_charge(case_id: str, confirm_fee: bool, fee_amount_cents: int) -> dict:
    """Charge 25% of the recorded reduction only after the user explicitly consents to the displayed fee."""
    return _call(api.reduction_confirmed, case_id, api.ChargeConsent(confirm_fee=confirm_fee, fee_amount_cents=fee_amount_cents))

@server.tool()
def start_bill_check(payload: dict | None = None) -> dict:
    """Start a conversational bill review using only the data supplied by this user."""
    return _call(api.start_draft, payload or {})

@server.tool()
def answer_question(case_id: str, answer: str) -> dict:
    """Answer the next bill-review intake question."""
    return _call(api.answer_question, case_id, api.DraftAnswer(answer=answer))

@server.tool()
def fee_quote(case_id: str) -> dict:
    """Read the exact USD fee on the case's stored reduction before asking for payment consent."""
    return _call(api.fee_quote, case_id)


@server.tool()
def delete_my_data(consent: bool) -> dict:
    """Delete this user's operational records after explicit confirmation. Required payment records are retained separately."""
    if consent is not True:
        raise ValueError("Explicit deletion confirmation required")
    return _call(api.delete_my_data)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_mcp_app(), host="127.0.0.1", port=int(os.environ.get("MCP_PORT", "8580")))
