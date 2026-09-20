"""Class Action Cash — MCP server (mcp SDK v2).

Every tool returns the same payload as the REST API, including the
`user_message` field the agent speaks verbatim.
"""
import os

from mcp.server.mcpserver import MCPServer

import core
from identity import IdentityMiddleware, require_owner

server = MCPServer("class-action-cash")


def create_mcp_app():
    from identity import IdentityMiddleware
    app = server.streamable_http_app(streamable_http_path="/mcp")
    app.add_middleware(IdentityMiddleware)
    return app


def main():
    import uvicorn
    port = int(os.environ.get("MCP_PORT", "8576"))
    uvicorn.run(create_mcp_app(), host="127.0.0.1", port=port, log_level="warning")


@server.tool()
def list_settlements() -> dict:
    """List currently-open class-action settlements with deadlines and freshness warning."""
    owner = require_owner()  # noqa: F841 - identity enforced for all tenant tools
    return core.get_settlements()


@server.tool()
def scan_receipts(source: str = "gmail") -> dict:
    """Scan receipts against open settlements. 'gmail' reads the mailbox (read-only);
    'fixtures' uses demo receipts."""
    owner = require_owner()
    return core.scan(source, owner=owner)


@server.tool()
def get_matches() -> dict:
    """List all settlement matches found so far."""
    owner = require_owner()
    return core.list_matches(owner=owner)


@server.tool()
def generate_claim_pack(match_id: str, full_name: str = "", email: str = "",
                        address: str = "") -> dict:
    """Build the filing pack for a match. The USER files the claim; this never files anything."""
    owner = require_owner()
    try:
        return core.generate_claim_pack(match_id, full_name, email, address, owner=owner)
    except KeyError as e:
        return {"ok": False, "error": str(e),
                "user_message": "I couldn't find that match — try listing matches first."}


@server.tool()
def setup_billing(match_id: str, name: str, email: str = "") -> dict:
    """Save a card for the later fee. Responds with the exact fee disclosure first:
    20% of the confirmed payout, charged only if the user confirms the payout."""
    owner = require_owner()
    try:
        return core.setup_billing(match_id, name, email, owner=owner)
    except KeyError as e:
        return {"ok": False, "error": str(e),
                "user_message": "I couldn't find that match — try listing matches first."}


@server.tool()
def confirm_payout(match_id: str, amount: float) -> dict:
    """User confirms the settlement paid out. Charges the 20% fee off-session."""
    owner = require_owner()
    try:
        return core.confirm_payout(match_id, amount, owner=owner)
    except (KeyError, ValueError) as e:
        return {"ok": False, "error": str(e),
                "user_message": "Couldn't confirm that payout: " + str(e)}


@server.tool()
def receive_life_event(event_type: str, payload: dict) -> dict:
    """Shared-bus receiver: log a detected life event, create a draft match,
    and return the proactive nudge."""
    owner = require_owner()
    return core.receive_life_event(event_type, payload or {}, owner=owner)


if __name__ == "__main__":
    main()
