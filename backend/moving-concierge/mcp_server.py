"""MCP server for Moving Concierge (SDK v2, streamable HTTP).

Tools mirror the REST API; each tool result includes `user_message`, a warm
sentence the agent can speak verbatim. The agent is the UI.
"""
from mcp.server.mcpserver import MCPServer

import app as api
from src import db
from src.identity import require_owner

server = MCPServer("moving-concierge")


def create_mcp_app():
    from src.identity import IdentityMiddleware
    mcp_app = server.streamable_http_app(streamable_http_path="/mcp")
    mcp_app.add_middleware(IdentityMiddleware)
    return mcp_app


@server.tool()
def create_move(name: str, email: str, old_address: str, new_address: str,
                move_date: str, state: str, draft_id: str | None = None) -> dict:
    """Start a move pack: builds the address-change checklist, fans the move out to the suite.

    state: 2-letter code for the NEW address (drives DMV deadline + voter links).
    draft_id: draft move id from a life-event nudge, to upgrade it in place.
    """
    owner = require_owner()
    payload = api.MoveIn(name=name, email=email, old_address=old_address,
                         new_address=new_address, move_date=move_date,
                         state=state, draft_id=draft_id)
    return api.create_move(payload)


@server.tool()
def get_move(move_id: str) -> dict:
    """Get move details, checklist progress, and items."""
    owner = require_owner()
    return api.get_move(move_id)


@server.tool()
def get_pack(move_id: str) -> dict:
    """Fetch the address-change pack (markdown + PDF link). Unlocks after the $49 fee is paid."""
    owner = require_owner()
    return api.get_pack(move_id, format="md")


@server.tool()
def update_checklist(move_id: str, items: list[dict]) -> dict:
    """Update checklist item statuses. items: [{"item_id": 1, "status": "done"|"pending"|"na"}]."""
    owner = require_owner()
    payload = api.ChecklistPatch(items=[api.ChecklistItemUpdate(**i) for i in items])
    return api.update_checklist(move_id, payload)


@server.tool()
def setup_billing(move_id: str) -> dict:
    """Set up billing for the $49 pack fee: creates the Stripe customer and card SetupIntent.

    Returns the client_secret for card collection. No charge happens here.
    """
    owner = require_owner()
    return api.billing_setup(move_id)


@server.tool()
def pay(move_id: str) -> dict:
    """Charge the flat $49.00 pack fee off-session against the saved card, then deliver the pack.

    Idempotent: an already-paid move returns an error, never a second charge.
    """
    owner = require_owner()
    return api.pay(move_id)


@server.tool()
def billing_status(move_id: str) -> dict:
    """Poll billing state: card state + whether the $49 pack fee is settled."""
    owner = require_owner()
    return api.billing_status(move_id)


@server.tool()
def receive_life_event(event_type: str, payload: dict) -> dict:
    """Receive a fanned-out life event from the shared bus. A 'move' creates a draft move and the proactive nudge."""
    owner = require_owner()
    res = api.life_event(api.LifeEventIn(event_type=event_type, payload=payload))
    # Drafts created without an owner are claimed by the calling user.
    if isinstance(res, dict) and res.get("move_id"):
        db.adopt_move(res["move_id"], owner)
    return res


if __name__ == "__main__":
    import os as _os

    import uvicorn

    port = int(_os.environ.get("MCP_PORT", "8578"))
    uvicorn.run(create_mcp_app(), host="127.0.0.1", port=port, log_level="warning")
