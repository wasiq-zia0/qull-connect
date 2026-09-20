"""Connector routing suggestions without implicit cross-customer data sharing.

This module does not dispatch events or persist customer payloads. An integrator
may send a user-authorized event to a destination's /api/life-events endpoint
using that customer's scoped bearer key. Each destination validates its input
and assigns ownership from authentication, never from the event payload.
"""

import uuid

EVENT_FANOUT = {
    "move": ["deposit-recovery", "moving-concierge", "unclaimed-property"],
    "flight_delayed": ["eu261-flight-comp"],
    "job_change": ["final-paycheck", "401k-match"],
    "bill_spike": ["bill-negotiator"],
    "recurring_charge_detected": ["subscription-slayer"],
    "medical_bill_received": ["medical-bill-fighter"],
    "settlement_match": ["class-action-cash"],
}


def emit(event_type: str, payload: dict, source: str = "muse") -> dict:
    """Compatibility helper: suggest destinations, without sharing the payload."""
    return {"event_id": uuid.uuid4().hex,
            "suggested_connectors": list(EVENT_FANOUT.get(event_type, [])),
            "fanned_out_to": [], "dispatched": False,
            "user_authorization_required": True}


def recent(limit: int = 50) -> list:
    """No shared event history is maintained or exposed."""
    return []
