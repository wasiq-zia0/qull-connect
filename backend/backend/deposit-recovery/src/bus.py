"""Shared life-event bus integration.

Best-effort: emitting an event must never break the caller, and the connector
works standalone when the bus isn't present.
"""
import sys
from pathlib import Path

try:
    sys.path.insert(0, str(Path.home() / "workspace/connectors/life-events"))
    import events as _events
except ImportError:  # pragma: no cover
    _events = None


def emit_move(payload: dict) -> dict | None:
    """Log a detected move to the shared bus; returns fan-out info or None."""
    if _events is None:
        return None
    try:
        return _events.emit("move", payload, source="deposit-recovery")
    except Exception:
        return None
