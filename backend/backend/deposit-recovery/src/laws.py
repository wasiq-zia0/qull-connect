"""State law database loader."""
import json
from pathlib import Path

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "state_laws.json"

with open(_DATA_PATH) as f:
    _raw = json.load(f)

STATES = {s["abbr"]: s for s in _raw["states"]}
META = _raw["_meta"]


def get_state(abbr: str) -> dict:
    abbr = abbr.strip().upper()
    if abbr not in STATES:
        raise KeyError(f"Unknown state abbreviation: {abbr}")
    return STATES[abbr]


def list_states() -> list:
    return sorted(STATES.values(), key=lambda s: s["state"])


# deadline_basis values whose legal clock runs from the date the tenant
# provides a forwarding address (TX, CT, MN, WY as of this data file).
FORWARDING_DEPENDENT_BASIS = frozenset(
    {"forwarding_address", "later_of_move_out_or_forwarding"}
)


def requires_forwarding_date(abbr: str) -> bool:
    """True when the state's deadline clock runs from the tenant's forwarding
    address, so no deadline can be computed without one."""
    return get_state(abbr)["deadline_basis"] in FORWARDING_DEPENDENT_BASIS
