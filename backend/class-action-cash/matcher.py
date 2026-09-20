"""Receipt matcher: Gmail (read-only) or local fixtures.

HARD RULE: Gmail access is strictly read-only. Only search/list/read
commands are used. Never send, reply, forward, trash, or mark mail.
"""
import json
import re
import subprocess

from safety import sanitize

GMAIL_CLI = ["hatch_gws_cli", "gmail"]


def _gmail_query(keywords: list) -> str:
    # Quote each keyword and OR them. Keywords come from our curated
    # settlements file, but are still sanitized so they can never alter
    # the query structure beyond the intended OR list.
    terms = []
    for kw in keywords:
        k = sanitize(kw, 60).replace('"', "")
        if k:
            terms.append(f'"{k}"')
    return "(" + " OR ".join(terms) + ")" if terms else ""


def search_gmail(settlements: list, max_per_settlement: int = 8) -> list:
    """Search the user's Gmail for receipts matching each settlement."""
    candidates = []
    for s in settlements:
        query = _gmail_query(s.get("match_keywords", []))
        if not query:
            continue
        try:
            proc = subprocess.run(
                [*GMAIL_CLI, "+triage", "--query", query, "--max",
                 str(max_per_settlement), "--format", "json"],
                capture_output=True, text=True, timeout=60)
        except Exception as e:
            candidates.append({"_error": f"gmail search failed: {e}"})
            continue
        try:
            data = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            continue
        messages = data.get("messages") or data.get("results") or []
        for m in messages:
            candidates.append({
                "settlement_name": s["name"],
                "claim_deadline": s.get("claim_deadline", ""),
                "matched_keyword": ", ".join(s.get("match_keywords", [])),
                "typical_payout_range": s.get("typical_payout_range", ""),
                "official_claim_url": s.get("official_claim_url", ""),
                "id": str(m.get("id", "")),
                "from": m.get("from", "") or m.get("sender", ""),
                "subject": m.get("subject", ""),
                "date": m.get("date", ""),
                "snippet": m.get("snippet", "") or m.get("preview", ""),
            })
    return [c for c in candidates if "_error" not in c]


def search_fixtures(settlements: list) -> list:
    """Deterministic matcher used for demos and tests (no Gmail involved)."""
    import pathlib
    fx = json.loads(pathlib.Path(__file__).resolve().parent
                    .joinpath("data/fixtures/gmail_receipts.json").read_text())
    candidates = []
    for s in settlements:
        for kw in s.get("match_keywords", []):
            needle = kw.lower()
            for r in fx:
                hay = f"{r.get('subject','')} {r.get('from','')} {r.get('snippet','')}".lower()
                if needle in hay:
                    candidates.append({
                        "settlement_name": s["name"],
                        "claim_deadline": s.get("claim_deadline", ""),
                        "matched_keyword": kw,
                        "typical_payout_range": s.get("typical_payout_range", ""),
                        "official_claim_url": s.get("official_claim_url", ""),
                        "id": r.get("id", ""),
                        "from": r.get("from", ""),
                        "subject": r.get("subject", ""),
                        "date": r.get("date", ""),
                        "snippet": r.get("snippet", ""),
                    })
                    break  # one candidate per keyword is enough
    # de-dupe by (settlement, receipt)
    seen, out = set(), []
    for c in candidates:
        key = (c["settlement_name"], c["id"])
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out
