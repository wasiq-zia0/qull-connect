"""State-specific DMV + voter-registration data (data/voter_links.json)."""
import json
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "voter_links.json"

with DATA_PATH.open() as f:
    STATES: dict = json.load(f)


def get_state(abbr: str) -> dict:
    """Return the state record; raises KeyError for unknown abbreviations."""
    key = (abbr or "").strip().upper()
    if key not in STATES:
        raise KeyError(f"Unknown state abbreviation: {abbr!r}")
    return STATES[key]


def list_states() -> list[dict]:
    return [{"abbr": s["abbr"], "state": s["state"],
             "dmv_deadline": s["dmv_deadline"]} for s in STATES.values()]
