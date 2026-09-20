"""Negotiation script generator: loads general, conditional talking points and
assembles a call script + chat script pack for the user.

The USER makes the call/chat; this connector never contacts providers.
Scripts are negotiation guidance only, not legal or financial advice.
"""
import json
import re
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "provider_scripts.json"
DISCLAIMER = ("Scripts are negotiation guidance only, not legal or financial advice. "
              "The connector never contacts providers on your behalf — you make the call or chat yourself.")


def clean_text(value: str, max_len: int = 200) -> str:
    """Treat all user-supplied text as untrusted data: strip control characters,
    collapse whitespace, and cap length before it is stored or rendered into output."""
    if not isinstance(value, str):
        return value
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:max_len]


def _load() -> dict:
    return json.loads(DATA_PATH.read_text())


def normalize_provider(provider: str) -> str:
    """Match a provider name to a known key; fall back to 'generic'."""
    blob = _load()
    query = provider.strip().lower()
    for key, info in blob["providers"].items():
        if query == key or query in {n.lower() for n in info.get("names", [])}:
            return key
        if any(query in n.lower() or n.lower() in query for n in info.get("names", [])):
            return key
    return "generic"


def script_pack(provider: str, service_type: str, current_monthly_bill: float,
                account_tenure_months: int | None) -> dict:
    blob = _load()
    key = normalize_provider(provider)
    entry = blob["providers"][key] if key in blob["providers"] else blob["generic"]
    generic = blob["generic"]
    tenure_line = (f"You have been a customer for {account_tenure_months} months — "
                   "lead with loyalty.")
    if account_tenure_months is None:
        tenure_line = "If you're a long-time customer, lead with loyalty and on-time payment history."
    return {
        "provider": clean_text(entry.get("names", [provider])[0]),
        "provider_matched_as": key,
        "service_type": clean_text(service_type, max_len=60),
        "current_monthly_bill": current_monthly_bill,
        "retention_note": entry.get("retention_note"),
        "before_you_call": [
            tenure_line,
            f"Know your current bill: ${current_monthly_bill:,.2f}/mo for {service_type}.",
            "If available, compare one real competitor offer for equivalent service, including fees and contract terms.",
            "Check the current offer terms; no time of month or escalation is guaranteed to produce a discount.",
        ],
        "call_script": entry.get("call_script") or generic["call_script"],
        "chat_script": entry.get("chat_script") or generic["chat_script"],
        "talking_points": entry.get("talking_points") or generic["talking_points"],
        "after_the_call": [
            "Confirm the new monthly bill, the effective date, and how many months the rate is locked.",
            "Get the confirmation number and the rep's name.",
            "Report the outcome back here (even 'no success') so savings — and the fee — are accurate.",
        ],
        "disclaimer": DISCLAIMER,
    }


def list_providers() -> list[dict]:
    blob = _load()
    return [{"key": key, "names": info.get("names", [])} for key, info in blob["providers"].items()]
