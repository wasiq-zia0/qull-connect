"""Shared life-events bus integration (~/workspace/connectors/life-events).

Contract:
- EMIT when this connector detects a move (direct intake via POST /api/moves).
  Do NOT re-emit for moves received via POST /api/life-events — the bus already
  fanned those out.
- RECEIVE via POST /api/life-events (implemented in app.py).
"""
import sys
from pathlib import Path


def _bus():
    sys.path.insert(0, str(Path.home() / "workspace/connectors/life-events"))
    import events as life_events
    return life_events


def emit_move(move: dict) -> dict:
    """Log a move event; returns {'event_id', 'fanned_out_to'}."""
    bus = _bus()
    return bus.emit("move", {
        "move_id": move["id"],
        "name": move["name"],
        "from_address": move["old_address"],
        "to_address": move["new_address"],
        "state": move["state"],
        "move_date": move["move_date"],
    }, source="moving-concierge")
