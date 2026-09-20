"""Scan source adapters.

Production: read-only Gmail search via the hatch_gws_cli gmail skill
(triage for candidates, read for bodies). Never sends, replies, forwards,
deletes, or labels anything.

Testing / unauthenticated: load JSON fixtures from data/fixtures/ instead.
The API accepts {"source": "gmail"|"fixtures"} so reviewers can exercise
the full pipeline deterministically.
"""
import json
import re
import subprocess
from pathlib import Path

from detector import RECEIPT_KEYWORDS, detect_recurring, is_receipt_candidate

BASE_DIR = Path(__file__).resolve().parent
FIXTURE_DIR = BASE_DIR / "data" / "fixtures"

GMAIL_CLI = ["hatch_gws_cli", "gmail"]

SCAN_QUERY = (
    '(subject:receipt OR subject:invoice OR subject:"payment received" '
    'OR subject:"your subscription" OR subject:billing OR subject:renewal '
    'OR subject:"payment confirmation") '
    "-category:promotions -category:social newer_than:400d"
)


def _run_cli(*args: str, timeout: int = 60) -> dict:
    proc = subprocess.run([*GMAIL_CLI, *args], capture_output=True, text=True, timeout=timeout)
    out = proc.stdout.strip()
    if not out:
        return {"ok": False, "error": proc.stderr.strip()[:300] or "empty gmail output"}
    try:
        start = out.index("{")
        return json.loads(out[start:])
    except (ValueError, IndexError):
        return {"ok": False, "error": f"unparseable gmail output: {out[:200]}"}


def gmail_status() -> dict:
    """Check whether Gmail is connected (read-only status check)."""
    return _run_cli("status")


def scan_gmail(max_results: int = 100) -> dict:
    """Run the receipt search against the connected Gmail account.

    Read-only: only +triage (list) and +read (bodies of candidates).
    Returns {"receipts": [...]} or {"error": ...}.
    """
    status = gmail_status()
    if not status.get("ok") or status.get("status") != "connected":
        return {"error": "gmail_not_connected",
                "detail": "Gmail is not connected. Connect via the Custom connector flow, or scan with source='fixtures'."}
    triage = _run_cli("+triage", "--query", SCAN_QUERY, "--max", str(max_results), "--format", "json")
    candidates = triage.get("results") or triage.get("messages") or []
    if isinstance(triage, list):
        candidates = triage
    receipts: list[dict] = []
    for c in candidates[:max_results]:
        mid = c.get("id") or c.get("message_id")
        if not mid:
            continue
        subject = c.get("subject", "")
        sender = c.get("from", "") or c.get("sender", "")
        snippet = c.get("snippet", "")
        if not is_receipt_candidate(subject, snippet, sender):
            # Pull metadata only for borderline candidates; skip obvious junk.
            continue
        body = ""
        detail = _run_cli("+read", "--id", mid, "--format", "json")
        if isinstance(detail, dict) and detail.get("ok") is not False:
            body = detail.get("body") or detail.get("snippet") or ""
        date_str = ""
        raw_date = detail.get("date") if isinstance(detail, dict) else ""
        m = re.search(r"\d{4}-\d{2}-\d{2}", str(raw_date or c.get("date") or ""))
        if m:
            date_str = m.group(0)
        receipts.append({
            "subject": subject, "sender": sender, "snippet": snippet,
            "body": body[:4000], "date": date_str,
        })
    return {"receipts": receipts}


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
