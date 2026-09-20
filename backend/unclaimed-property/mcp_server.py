"""Unclaimed-property connector: MCP server (streamable HTTP, port 8577).

Mirrors the REST actions as MCP tools so a Muse agent can drive the whole
flow conversationally. Every tool result carries a `user_message` field — a
warm, speakable sentence — alongside the machine data.

Run:  .venv/bin/python mcp_server.py   (or via run.py for REST + MCP together)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from mcp.server.mcpserver import MCPServer

import app
import store
from identity import require_owner, IdentityError

store.init()

server = MCPServer(
    name="unclaimed-property",
    title="Unclaimed Property Finder",
    description="Finds unclaimed funds across all 50 states + DC, builds per-state claim "
                "packs the user files themselves, and bills a 10% (state-capped) contingency "
                "fee on user-confirmed recoveries.",
    version="0.2.0",
)


def create_mcp_app():
    from identity import IdentityMiddleware
    mcp_app = server.streamable_http_app(streamable_http_path="/mcp")
    mcp_app.add_middleware(IdentityMiddleware)
    return mcp_app


def _unwrap(resp) -> dict:
    """Normalize FastAPI route results (dict or JSONResponse) to a plain dict."""
    if isinstance(resp, JSONResponse):
        return json.loads(resp.body.decode())
    return resp


def _call(fn, *args, **kwargs) -> dict:
    try:
        return _unwrap(fn(*args, **kwargs))
    except IdentityError as e:
        return {"error": "unauthorized", "message": str(e),
                "user_message": str(e)}
    except HTTPException as e:
        return {"error": "http_error", "message": e.detail,
                "user_message": "Something didn't line up — let's try that again."}


@server.tool()
def list_states() -> dict:
    """List all 50 states + DC with their official unclaimed-property portals."""
    owner = require_owner()
    return _call(app.list_states)


@server.tool()
def start_search(full_legal_name: str, email: str, states: str,
                 prior_names: str = "", dob: str = "", dob_consent: bool = False) -> dict:
    """Start an unclaimed-property search.
    states: JSON list like '[{"abbr":"TX","years":"2018-2021"}]'.
    prior_names: comma-separated maiden/prior names (optional).
    dob: YYYY-MM-DD only with dob_consent=true (some states need it at filing).
    """
    owner = require_owner()
    req = app.IntakeRequest(
        full_legal_name=full_legal_name,
        prior_names=[p.strip() for p in prior_names.split(",") if p.strip()],
        email=email,
        states_of_residence=json.loads(states),
        dob=dob or None,
        dob_consent=dob_consent,
    )
    return _call(app.create_search, req)


@server.tool()
def handle_life_event(event_type: str, payload: str) -> dict:
    """Handle a fanned-out life event from the shared bus.
    payload: JSON object, e.g. '{"from_state":"TX","to_state":"CO"}'.
    A 'move' pre-fills the state checklist and returns the proactive nudge."""
    owner = require_owner()
    evt = app.LifeEvent(event_type=event_type, payload=json.loads(payload))
    res = _call(app.receive_life_event, evt)
    # Drafts created without an owner are claimed by the calling user.
    if isinstance(res, dict) and res.get("search_id"):
        store.adopt_search(res["search_id"], owner)
    return res


@server.tool()
def complete_search(search_id: str, full_legal_name: str) -> dict:
    """The one-tap confirmation after a move nudge: supply the claimant's full
    legal name to activate a draft search and generate claim packs."""
    owner = require_owner()
    body = app.DraftUpdate(full_legal_name=full_legal_name)
    return _call(app.update_search, search_id, body)


@server.tool()
def get_claim_pack(search_id: str, state: str) -> dict:
    """Get the claim pack for one state: portal link, filing steps, document
    checklist, and a pre-filled cover sheet. The user files; never the connector."""
    owner = require_owner()
    return _call(app.claim_pack, search_id, state)


@server.tool()
def update_claim_status(search_id: str, state: str, status: str) -> dict:
    """Update a state's claim status: not_started, in_progress, filed, paid, denied."""
    owner = require_owner()
    body = app.StatusUpdate(status=status)
    return _call(app.update_state_status, search_id, state, body)


@server.tool()
def setup_billing(search_id: str) -> dict:
    """Set up billing: creates the Stripe customer + SetupIntent for an off-session
    card save. States the exact fee BEFORE the card is saved."""
    owner = require_owner()
    return _call(app.billing_setup, search_id)


@server.tool()
def confirm_recovery(search_id: str, amount: float, state: str = "") -> dict:
    """Confirm a recovery and charge the 10% (state-capped) contingency fee off-session.
    Requires billing/setup to have been completed first."""
    owner = require_owner()
    body = app.RecoveryConfirmed(amount=amount, state_abbr=state or None)
    return _call(app.recovery_confirmed, search_id, body)


@server.tool()
def billing_status(search_id: str) -> dict:
    """Poll billing state: card state + whether the recovery fee is settled."""
    owner = require_owner()
    return _call(app.billing_status, search_id)


if __name__ == "__main__":
    import os as _os

    import uvicorn

    port = int(_os.environ.get("MCP_PORT", "8577"))
    uvicorn.run(create_mcp_app(), host="127.0.0.1", port=port, log_level="warning")
