"""Match user-supplied receipt text only; never read shared host mail credentials."""
import json
from pathlib import Path


def search_receipts(settlements: list, receipts: list) -> list:
    candidates = []
    for settlement in settlements:
        for receipt in receipts:
            text = " ".join(str(receipt.get(k, "")) for k in ("subject", "sender", "snippet")).casefold()
            hits = [keyword for keyword in settlement.get("match_keywords", []) if keyword.casefold() in text]
            if hits:
                candidates.append({"settlement_name": settlement["name"],
                    "claim_deadline": settlement.get("claim_deadline"), "matched_keyword": ", ".join(hits),
                    "typical_payout_range": settlement.get("typical_payout_range", ""),
                    "official_claim_url": settlement["official_claim_url"],
                    "id": receipt.get("id", ""), "from": receipt.get("sender", ""),
                    "subject": receipt.get("subject", ""), "date": receipt.get("date", ""),
                    "snippet": receipt.get("snippet", "")})
    return candidates


def search_fixtures(settlements: list) -> list:
    receipts = json.loads((Path(__file__).parent / "data/fixtures/gmail_receipts.json").read_text())
    return search_receipts(settlements, [{**r, "sender": r.get("from", "")} for r in receipts])


def search_gmail(*args, **kwargs):
    raise ValueError("Gmail OAuth is not implemented. Supply receipt excerpts with source='receipts'.")
