"""Scan source adapters.

User-provided receipts are handled by the API. Gmail is unavailable until
a per-user authorization flow exists; no operator mailbox is ever accessed.

Testing / unauthenticated: load JSON fixtures from data/fixtures/ instead.
The API accepts {"source": "gmail"|"fixtures"} so reviewers can exercise
the full pipeline deterministically.
"""
import json
from pathlib import Path

from detector import RECEIPT_KEYWORDS, detect_recurring, is_receipt_candidate

BASE_DIR = Path(__file__).resolve().parent
FIXTURE_DIR = BASE_DIR / "data" / "fixtures"

def scan_gmail(max_results: int = 100) -> dict:
    """Unavailable until there is an authenticated per-user Gmail integration."""
    return {"error": "gmail_not_connected", "detail": "Per-user Gmail authorization is not implemented. Import receipts supplied by the user or add subscriptions manually."}


def load_fixtures() -> list[dict]:
    """Load the deterministic JSON fixture emails for testing/offline scans."""
    receipts = []
    if not FIXTURE_DIR.exists():
        return receipts
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        try:
            with open(path) as f:
                receipts.append(json.load(f))
        except (json.JSONDecodeError, OSError):
            continue
    return receipts


def scan(source: str = "gmail", max_results: int = 100) -> dict:
    """Unified scan entry: returns {"receipts": [...]} or {"error": ...}."""
    if source == "fixtures":
        return {"receipts": load_fixtures()}
    if source == "gmail":
        return scan_gmail(max_results=max_results)
    return {"error": "invalid_source", "detail": "source must be 'gmail' or 'fixtures'"}


def scan_and_detect(source: str = "gmail", max_results: int = 100) -> dict:
    """Scan, then run recurrence detection. Returns {"subscriptions": [...]}."""
    result = scan(source=source, max_results=max_results)
    if "error" in result:
        return result
    return {"subscriptions": detect_recurring(result["receipts"])}
