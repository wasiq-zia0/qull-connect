"""MCP server for the deposit-recovery connector (streamable HTTP).

Tools mirror the REST actions. Every tool result includes `user_message` — a
warm, ready-to-speak sentence the agent can say verbatim. The agent is the UI.

Security: all user-supplied values are untrusted data. They are validated,
never interpolated into queries (parameterized SQL only) or instructions, and
sanitized before rendering into PDFs.
"""
import argparse
import base64
import os
import uuid
from datetime import date
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from src.laws import get_state, requires_forwarding_date
from src.engine import case_status
from src.letters import build_pdf, sanitize
from src import db
from src.bus import emit_move
from src.billing import FEE_RATE
from src.identity import require_owner

server = MCPServer("deposit-recovery")
LETTER_DIR = Path(__file__).resolve().parent / "letters_out"
LETTER_DIR.mkdir(exist_ok=True)

db.init_db()

FORWARDING_MSG = (
    "forwarding_date is required in {abbr}: the legal deadline runs from the "
    "date you provided your forwarding address."
)


def _money(n: float) -> str:
    return f"${n:,.2f}"


def _nudge(status: dict) -> str:
    state, dep = status["state"], _money(status["deposit"])
    days = status.get("deadline_days")
    if status["status"] == "overdue":
        overdue = -status["days_remaining"]
        return (f"Your {state} landlord had {days} days to return your {dep} deposit. "
                f"It's day {overdue + (days or 0)} — the deadline passed {overdue} days ago. "
                "Want me to prepare the demand letter? You'll review and send it.")
    if status["status"] == "waiting":
        return (f"I'm tracking your {dep} deposit in {state}. Your landlord has until "
                f"{status['deadline']} ({status['days_remaining']} days) to return it. "
                "I'll draft the demand letter the moment that passes — nothing for you to do.")
    return (f"{state} doesn't set a fixed return deadline, so I've logged your case and "
            "we'll follow the notification process in the statute instead.")


def _parse(d: str | None, field: str) -> date | None:
    if not d:
        return None
    try:
        return date.fromisoformat(d)
    except ValueError:
        raise ValueError(f"{field} must be a YYYY-MM-DD date, got {d!r}")


@server.tool()
def create_case(tenant_name: str, tenant_forwarding_address: str, state: str,
                move_out: str, deposit: float, landlord_name: str,
                landlord_address: str, rental_address: str,
                forwarding_date: str | None = None) -> dict:
    """Open a deposit-recovery case after a move. In TX, CT, MN and WY the
    legal deadline runs from the forwarding address date, so forwarding_date
    (YYYY-MM-DD) is required there."""
    owner = require_owner()
    state = state.strip().upper()
    try:
        get_state(state)
    except KeyError:
        raise ValueError(f"Unknown state abbreviation: {state!r}")
    if requires_forwarding_date(state) and not forwarding_date:
        raise ValueError(FORWARDING_MSG.format(abbr=state))
    move_out_d = _parse(move_out, "move_out")
    if not move_out_d:
        raise ValueError("move_out is required (YYYY-MM-DD)")
    fwd_d = _parse(forwarding_date, "forwarding_date")
    if deposit <= 0:
        raise ValueError("deposit must be greater than 0")
    cid = uuid.uuid4().hex[:12]
    db.insert_case(cid, {
        "tenant_name": sanitize(tenant_name).strip(),
        "tenant_forwarding_address": sanitize(tenant_forwarding_address).strip(),
        "state": state,
        "move_out": move_out_d.isoformat(),
        "forwarding_date": fwd_d.isoformat() if fwd_d else None,
        "deposit": deposit,
        "landlord_name": sanitize(landlord_name).strip(),
        "landlord_address": sanitize(landlord_address).strip(),
        "rental_address": sanitize(rental_address).strip(),
    }, owner_id=owner)
    emit_move({"state": state, "move_out": move_out_d.isoformat(),
               "deposit": deposit, "case_id": cid})
    status = case_status(state, move_out_d, deposit, fwd_d)
    return {"case_id": cid, **status, "user_message": _nudge(status)}


@server.tool()
def get_case_status(case_id: str) -> dict:
    """Current status of a case: deadline, days remaining/overdue, max recovery, next action."""
    owner = require_owner()
    case = db.get_case(case_id, owner_id=owner)
    if not case:
        raise ValueError(f"Unknown case_id: {case_id!r}")
    try:
        status = case_status(
            case["state"], date.fromisoformat(case["move_out"]), case["deposit"],
            date.fromisoformat(case["forwarding_date"]) if case.get("forwarding_date") else None)
    except ValueError as e:
        raise ValueError(str(e))
    return {"case_id": case_id, **status, "user_message": _nudge(status)}


@server.tool()
def generate_demand_letter(case_id: str) -> dict:
    """Generate the statute-citing demand letter PDF. Only works once the
    legal deadline has passed — otherwise raises an error explaining the wait."""
    owner = require_owner()
    case = db.get_case(case_id, owner_id=owner)
    if not case:
        raise ValueError(f"Unknown case_id: {case_id!r}")
    if requires_forwarding_date(case["state"]) and not case.get("forwarding_date"):
        raise ValueError(FORWARDING_MSG.format(abbr=case["state"]))
    try:
        status = case_status(
            case["state"], date.fromisoformat(case["move_out"]), case["deposit"],
            date.fromisoformat(case["forwarding_date"]) if case.get("forwarding_date") else None)
    except ValueError as e:
        raise ValueError(str(e))
    if status["status"] != "overdue":
        raise ValueError(
            f"Not yet — the {status['state']} deadline is "
            f"{status.get('deadline') or 'not fixed'} and the letter goes out the day after "
            f"it passes (status: {status['status']}).")
    path = LETTER_DIR / f"demand-letter-{case_id}.pdf"
    build_pdf(case, str(path))
    db.update_case(case_id, {"letter_status": "draft"}, owner_id=owner)
    law = get_state(case["state"])
    return {
        "case_id": case_id,
        "filename": f"demand-letter-{case_id}.pdf",
        "pdf_base64": base64.b64encode(path.read_bytes()).decode(),
        "letter_status": "draft",
        "user_message": (
            f"Your demand letter is ready — it cites {law['statute']} and gives your landlord "
            f"10 days to return your {_money(case['deposit'])}. Review it and send it yourself, "
            "then tell me the moment your deposit lands so I can close your case. "
            "This is template automation, not legal advice."),
    }


@server.tool()
def mark_letter_status(case_id: str, status: str) -> dict:
    """Record what the user did with the prepared demand letter.

    status: "approved" (reviewed, ready to send) or "sent-confirmed-by-user"
    (the user confirms they sent it). The connector never sends the letter —
    it only prepares it for the user's approval and sending.
    """
    owner = require_owner()
    if status not in ("approved", "sent-confirmed-by-user"):
        raise ValueError(
            f"status must be 'approved' or 'sent-confirmed-by-user', got {status!r}")
    case = db.get_case(case_id, owner_id=owner)
    if not case:
        raise ValueError(f"Unknown case_id: {case_id!r}")
    db.update_case(case_id, {"letter_status": status}, owner_id=owner)
    msg = ("Got it — the letter is approved and ready for you to send."
           if status == "approved"
           else "Confirmed — the letter is sent. I'll keep watching for your deposit.")
    return {"case_id": case_id, "letter_status": status, "user_message": msg}


@server.tool()
def get_state_law(state: str) -> dict:
    """Look up a state's deposit return deadline, statute, and penalty multiple."""
    owner = require_owner()
    state = state.strip().upper()
    try:
        law = get_state(state)
    except KeyError:
        raise ValueError(f"Unknown state abbreviation: {state!r}")
    basis = ("from the day you gave your landlord a forwarding address"
             if requires_forwarding_date(state) else "from your move-out day")
    return {**law, "requires_forwarding_date": requires_forwarding_date(state),
            "user_message": (f"In {law['state']}, landlords get {law['deadline_days']} days "
                             f"{basis} to return your deposit ({law['statute']}).")}


def create_mcp_app():
    """Build the MCP ASGI app with the identity middleware.

    Replaces ``server.run(transport="streamable-http")`` so every tool call
    passes through :class:`src.identity.IdentityMiddleware`, which resolves
    the platform user into a contextvar that tools read via
    ``require_owner()``. No identity -> the tool raises IdentityError.
    """
    from src.identity import IdentityMiddleware

    app = server.streamable_http_app(streamable_http_path="/mcp")
    app.add_middleware(IdentityMiddleware)
    return app


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser(description="Deposit-recovery MCP server (streamable HTTP)")
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("DEPOSIT_MCP_PORT", "8571")))
    ap.add_argument("--host", default=os.environ.get("DEPOSIT_HOST", "127.0.0.1"))
    args = ap.parse_args()
    uvicorn.run(create_mcp_app(), host=args.host, port=args.port,
                log_level="warning")


if __name__ == "__main__":
    main()
