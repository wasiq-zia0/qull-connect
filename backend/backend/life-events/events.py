"""Shared life-event bus for the connector suite.

One life event fans out to every connector that can act on it, so the ten
connectors feel like one intelligence instead of ten APIs.

Append-only JSONL log — no server, no dependencies. Each connector integrates
two ways:

1. EMIT — when your connector detects a life event (e.g. deposit-recovery sees
   a move), call emit("move", {...}). The event is logged and the fan-out list
   is returned so the agent knows which connectors to nudge.

2. RECEIVE — expose POST /api/life-events accepting {"event_type": ..., "payload": {...}}.
   Create a draft case from the payload and return a proactive nudge in the
   `user_message` field, e.g.:
   "Your Texas landlord had 30 days to return your $1,800 deposit. It's day 42.
    Want me to send the demand letter? One tap."

Import from a connector:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path.home() / "workspace/connectors/life-events"))
    import events as life_events
    life_events.emit("move", {"state": "TX", "deposit": 1800, ...})
"""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

DIR = Path(__file__).resolve().parent
LOG = DIR / "events.jsonl"

# Life event -> connectors that should act on it.
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
    """Log a life event; returns event_id and the connectors fanned out to."""
    event = {
        "event_id": uuid.uuid4().hex[:12],
        "event_type": event_type,
        "payload": payload,
        "source": source,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    with LOG.open("a") as f:
        f.write(json.dumps(event) + "\n")
    return {"event_id": event["event_id"],
            "fanned_out_to": EVENT_FANOUT.get(event_type, [])}


def recent(limit: int = 50) -> list:
    """Read the most recent events (newest last)."""
    if not LOG.exists():
        return []
    lines = LOG.read_text().splitlines()[-limit:]
    return [json.loads(l) for l in lines if l.strip()]
