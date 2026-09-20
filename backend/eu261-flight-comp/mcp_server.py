"""MCP server for eu261-flight-comp (official mcp SDK v2, streamable-http).

One tool per action. All user-supplied text is treated as untrusted data and
escaped before PDF rendering (src/pack.py). No raw secrets anywhere.

Every tool resolves the calling user first (``require_owner()``) and scopes
all database access by that owner. The MCP ASGI app is built by
``create_mcp_app()``, which wraps the streamable-HTTP app in
``IdentityMiddleware`` — no identity means the tool raises IdentityError.
"""
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server.mcpserver import MCPServer  # noqa: E402

from src.identity import IdentityMiddleware, require_owner  # noqa: E402
from src import db, billing, eligibility, pack  # noqa: E402
import app as rest  # noqa: E402  (shares speak_verdict / payload helpers with REST)

sys.path.insert(0, str(Path.home() / "workspace/connectors/life-events"))
import events as life_events  # noqa: E402

server = MCPServer(name="eu261-flight-comp")
PACK_DIR = Path(__file__).resolve().parent / "claim_packs"
PACK_DIR.mkdir(exist_ok=True)


def _normalize_iata(v: str) -> str:
    return str(v or "").strip().upper()


@server.tool(description="Check a flight disruption and get the EU261 eligibility verdict + compensation amount.")
def check_eligibility(passenger_name: str, passenger_email: str, flight_number: str,
                      flight_date: str, airline: str, from_iata: str, to_iata: str,
                      disruption: str, arrival_delay_h: float = 0,
                      airline_eu_licensed: bool = False,
                      extraordinary_circumstances: bool = False) -> dict:
    owner = require_owner()
    if disruption not in ("delay", "cancellation", "denied_boarding"):
        raise ValueError(
            f"disruption must be delay, cancellation, or denied_boarding, got {disruption!r}")
    try:
        datetime.strptime(flight_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        raise ValueError(f"flight_date must be YYYY-MM-DD, got {flight_date!r}")
    if disruption == "delay" and not arrival_delay_h:
        raise ValueError("For a delay, arrival_delay_h (hours late) is required")
    data = {
        "passenger_name": passenger_name, "passenger_email": passenger_email,
        "flight_number": str(flight_number).strip().upper(),
        "flight_date": flight_date, "airline": airline,
        "airline_eu_licensed": airline_eu_licensed,
        "from_iata": _normalize_iata(from_iata), "to_iata": _normalize_iata(to_iata),
        "distance_km": None,
        "disruption": disruption,
        "scheduled_arrival": None, "actual_arrival": None,
        "arrival_delay_h": arrival_delay_h,
        "notice_days": None, "rerouted": False,
        "reroute_dep_early_h": None, "reroute_arr_delay_h": None,
        "extraordinary_circumstances": extraordinary_circumstances,
    }
    for key, label in (("from_iata", "departure"), ("to_iata", "arrival")):
        if not eligibility.lookup_airport(data[key]):
            raise ValueError(f"Unknown {label} airport {data[key]!r}; provide a valid IATA code")
    data = rest._payload_with_computed_delay(data)
    cid = db.create_claim(data, owner_id=owner)
    verdict = eligibility.verdict(data)
    if verdict["eligible"]:
        life_events.emit("flight_delayed", {
            "claim_id": cid,
            "flight_number": data.get("flight_number"),
            "route": f"{data.get('from_iata')}->{data.get('to_iata')}",
            "compensation_eur": verdict["compensation_eur"],
        }, source="eu261-flight-comp")
    return {"claim_id": cid, **verdict,
            "user_message": rest.speak_verdict(verdict, data)}


@server.tool(description="Get the eligibility verdict for a stored claim, plus its billing state.")
def get_claim(claim_id: str) -> dict:
    owner = require_owner()
    claim = db.get_claim(claim_id, owner_id=owner)
    if not claim:
        return {"error": "Unknown claim",
                "user_message": "I couldn't find that claim — want to check a flight?"}
    verdict = eligibility.verdict(claim["payload"])
    return {"claim_id": claim_id, **verdict,
            "billing_status": claim.get("billing_status") or "none",
            "payout_amount_eur": claim.get("payout_amount_eur"),
            "fee_amount_eur": claim.get("fee_amount_eur"),
            "payment_intent_id": claim.get("payment_intent_id"),
            "user_message": rest.speak_verdict(verdict, claim["payload"])}


@server.tool(description="Generate the EU261 claim letter pack PDF (only for eligible claims).")
def generate_claim_pack(claim_id: str) -> dict:
    owner = require_owner()
    claim = db.get_claim(claim_id, owner_id=owner)
    if not claim:
        return {"generated": False, "error": "Unknown claim",
                "user_message": "I couldn't find that claim — want to check a flight?"}
    verdict = eligibility.verdict(claim["payload"])
    if not verdict["eligible"]:
        return {"generated": False,
                "error": "Claim pack is only generated for eligible claims.",
                "user_message": "That claim doesn't qualify, so there's no pack to generate. Ask me why and I'll explain."}
    path = PACK_DIR / f"eu261-claim-pack-{claim_id}.pdf"
    pack.build_pdf(claim["payload"], verdict, str(path))
    dest = rest._city(verdict["to"]["iata"])
    return {"claim_id": claim_id, "generated": True,
            "pdf": f"eu261-claim-pack-{claim_id}.pdf",
            "user_message": (f"Done — your €{verdict['compensation_eur']} claim letter for the "
                             f"{dest} flight is ready. Just sign it and send it to the airline; "
                             "their claims email is usually on their website.")}


@server.tool(description="Set up billing: create the Stripe customer and card SetupIntent (no charge). Returns the plain-English fee disclosure before the card is saved.")
def setup_billing(claim_id: str) -> dict:
    owner = require_owner()
    claim = db.get_claim(claim_id, owner_id=owner)
    if not claim:
        return {"error": "Unknown claim",
                "user_message": "I couldn't find that claim — want to check a flight?"}
    if claim.get("stripe_customer_id"):
        return {"error": "Billing already set up for this claim",
                "user_message": "Billing is already set up for this claim — no need to do it twice."}
    payload = claim["payload"]
    cust = billing.setup_customer(payload.get("passenger_name", ""),
                                  payload.get("passenger_email", ""))
    if "error" in cust:
        return {"error": f"Stripe customer creation failed: {cust['error']}",
                "user_message": "I hit a snag setting up billing on Stripe's side. Let's try again in a moment."}
    setup = billing.create_card_setup(
        cust["customer_id"],
        idempotency_key=f"{billing.CONNECTOR}-setup-{owner}-{claim_id}")
    if "error" in setup:
        return {"error": f"Stripe card setup failed: {setup['error']}",
                "user_message": "I hit a snag setting up billing on Stripe's side. Let's try again in a moment."}
    db.update_claim(claim_id, owner_id=owner, stripe_customer_id=cust["customer_id"],
                    billing_status="card_pending")
    return {"claim_id": claim_id,
            "stripe_customer_id": cust["customer_id"],
            "client_secret": setup["client_secret"],
            "fee_disclosure": rest.FEE_DISCLOSURE,
            "user_message": ("Before you save your card, the plain-English deal: "
                             + rest.FEE_DISCLOSURE)}


@server.tool(description="Confirm the airline paid out; charge the 30% contingency fee off-session. "
                         "The fee is computed from the stored EU261 tier — the asserted amount must match it.")
def confirm_payout(claim_id: str, amount_eur: float) -> dict:
    owner = require_owner()
    claim = db.get_claim(claim_id, owner_id=owner)
    if not claim:
        return {"error": "Unknown claim",
                "user_message": "I couldn't find that claim — want to check a flight?"}
    if not claim.get("stripe_customer_id"):
        return {"error": "Set up billing first",
                "user_message": "There's no card on file yet — set up billing first, then confirm the payout."}
    if claim.get("billing_status") == "fee_charged":
        return {"error": "Fee already charged for this claim",
                "user_message": "The fee for this claim was already charged — you're all settled."}
    stored_tier = eligibility.verdict(claim["payload"])["compensation_eur"]
    if float(amount_eur) != float(stored_tier):
        return {"error": f"Asserted payout €{amount_eur:.2f} differs from stored tier €{stored_tier}",
                "user_message": (f"That doesn't add up — you confirmed €{amount_eur:.2f}, but this "
                                 f"claim's EU261 tier is €{stored_tier}. Mind double-checking what "
                                 "the airline actually paid?")}
    cents = billing.fee_cents(stored_tier)
    res = billing.charge_fee(
        claim["stripe_customer_id"], cents,
        f"EU261 contingency fee (30%) on EUR {stored_tier:.2f} payout, claim {claim_id}",
        idempotency_key=f"{billing.CONNECTOR}-fee-{owner}-{claim_id}")
    if "error" in res:
        db.update_claim(claim_id, owner_id=owner, billing_status="fee_failed")
        return {"error": f"Stripe charge failed: {res['error']}",
                "user_message": "The charge didn't go through — your card wasn't billed. Let's try again in a moment."}
    db.update_claim(claim_id, owner_id=owner, billing_status="fee_charged",
                    payout_amount_eur=stored_tier,
                    fee_amount_eur=cents / 100,
                    payment_intent_id=res["payment_intent_id"])
    fee = round(cents / 100, 2)
    return {"claim_id": claim_id, "payout_eur": stored_tier,
            "fee_eur": fee, "fee_rate": billing.FEE_RATE,
            "payment_intent_id": res["payment_intent_id"], "status": res["status"],
            "user_message": (f"Confirmed — the airline paid €{stored_tier:.2f}, so our "
                             f"30% fee of €{fee:.2f} has been charged. Congrats on the win!")}


@server.tool(description="Receive a fanned-out life event (e.g. flight_delayed); creates a draft claim owned by the caller and returns the proactive nudge.")
def handle_life_event(event_type: str, payload: dict) -> dict:
    owner = require_owner()
    if event_type != "flight_delayed":
        return {"accepted": False, "event_type": event_type,
                "user_message": f"I don't handle '{event_type}' events — nothing to do here."}
    raw = dict(payload or {})
    for k in ("from_iata", "to_iata", "flight_number"):
        if raw.get(k):
            raw[k] = str(raw[k]).strip().upper()
    raw.setdefault("disruption", "delay")
    raw = rest._payload_with_computed_delay(raw)
    cid = db.create_claim({**raw, "_draft": True}, owner_id=owner)
    verdict = eligibility.verdict(raw)
    nudge = rest._nudge_for_draft(raw, verdict)
    return {"accepted": True, "claim_id": cid, "draft": True,
            "eligible": verdict["eligible"],
            "compensation_eur": verdict["compensation_eur"],
            "user_message": nudge}


def create_mcp_app():
    """Build the MCP ASGI app with the identity middleware.

    Every tool call passes through :class:`src.identity.IdentityMiddleware`,
    which resolves the platform user into a contextvar that tools read via
    ``require_owner()``. No identity -> the tool raises IdentityError.
    """
    app = server.streamable_http_app(streamable_http_path="/mcp")
    app.add_middleware(IdentityMiddleware)
    return app


def main() -> None:
    import argparse
    import uvicorn

    ap = argparse.ArgumentParser(description="EU261 MCP server (streamable HTTP)")
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("EU261_MCP_PORT", "8572")))
    ap.add_argument("--host", default=os.environ.get("EU261_HOST", "127.0.0.1"))
    args = ap.parse_args()
    uvicorn.run(create_mcp_app(), host=args.host, port=args.port,
                log_level="warning")


if __name__ == "__main__":
    main()
