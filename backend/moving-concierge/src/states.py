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
    record = dict(STATES[key])
    record["dmv_deadline"] = "after confirming the applicable deadline with the state DMV"
    record["voter_note"] = "Check the official election office for registration deadlines, eligibility, and available application methods."
    return record


def list_states() -> list[dict]:
    return [{"abbr": s["abbr"], "state": s["state"],
             "dmv_deadline": get_state(s["abbr"])["dmv_deadline"]} for s in STATES.values()]
