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

server = MCPServer("401k-match")


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
def create_plan(name: str, salary: float, pay_frequency: str, current_contrib_pct: float, match_pct: float, match_cap_pct: float, true_up: bool = False) -> dict:
    """Calculate a full-year educational match illustration from the user's confirmed plan formula. $99 one-time plan unlock; no subscription."""
    return _call(api.create_plan, api.PlanIn(name=name, salary=salary, pay_frequency=pay_frequency, current_contrib_pct=current_contrib_pct, match_pct=match_pct, match_cap_pct=match_cap_pct, true_up=true_up))

@server.tool()
def get_plan_summary(plan_id: str) -> dict:
    """Get the educational summary and paid status."""
    return _call(api.get_plan_summary, plan_id)

@server.tool()
def setup_billing(plan_id: str, accept_fee_terms: bool, email: str = "") -> dict:
    """Return a secure hosted card-save URL; disclose $99 one-time, with no automatic renewal."""
    return _call(api.billing_setup, plan_id, api.BillingSetupIn(email=email, accept_fee_terms=accept_fee_terms))

@server.tool()
def pay_fee(plan_id: str, confirm_fee: bool, fee_amount_cents: int) -> dict:
    """Charge the one-time $99 fee only after explicit user consent."""
    return _call(api.pay, plan_id, api.ChargeConsent(confirm_fee=confirm_fee, fee_amount_cents=fee_amount_cents))

@server.tool()
def get_fix_pack(plan_id: str) -> dict:
    """Return the unlocked match illustration and administrator-confirmation checklist."""
    return _call(api.get_pack, plan_id)

@server.tool()
def billing_status(plan_id: str) -> dict:
    """Verify card-save completion and paid status."""
    return _call(api.billing_status, plan_id)

@server.tool()
def start_draft() -> dict:
    """Start the educational match intake for this authenticated user."""
    return _call(api.start_draft)

@server.tool()
def answer_draft(draft_id: str, answer: str) -> dict:
    """Answer the current intake question. Example formulas are not asserted to describe the employer's plan."""
    return _call(api.answer_draft, draft_id, api.DraftAnswerIn(answer=answer))

@server.tool()
def delete_my_data(consent: bool) -> dict:
    """Delete this user's operational records after explicit confirmation. Required payment records are retained separately."""
    if consent is not True:
        raise ValueError("Explicit deletion confirmation required")
    return _call(api.delete_my_data)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(create_mcp_app(), host="127.0.0.1", port=int(os.environ.get("MCP_PORT", "8580")))
