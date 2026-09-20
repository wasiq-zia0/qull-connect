"""Detection engine: extract receipt fields from emails, group by merchant,
and flag recurring subscription patterns.

All functions here are pure and testable. User-supplied / email text is
treated as untrusted data: nothing is eval'd, exec'd, or turned into
instructions; text is only matched against regexes and grouped by a
normalised merchant key.
"""
import re
from collections import defaultdict
from datetime import date, timedelta

RECEIPT_KEYWORDS = [
    "receipt", "invoice", "payment received", "your subscription", "billing",
    "renewal", "payment confirmation", "thanks for your payment",
    "you were charged", "subscription receipt", "membership receipt",
]

# Known merchant normalisations: fragment of sender/subject -> canonical key.
MERCHANT_ALIASES = {
    "netflix": "netflix", "spotify": "spotify", "hulu": "hulu",
    "disney+": "disney_plus", "disney plus": "disney_plus",
    "amazon prime": "amazon_prime", "primevideo": "amazon_prime",
    "audible": "audible", "apple": "apple_services", "icloud": "apple_services",
    "google one": "google_one", "youtube premium": "youtube_premium",
    "microsoft": "microsoft_365", "office 365": "microsoft_365",
    "adobe": "adobe", "creative cloud": "adobe",
    "dropbox": "dropbox", "github": "github", "notion": "notion",
    "slack": "slack", "zoom": "zoom", "figma": "figma",
    "chatgpt": "chatgpt_plus", "openai": "chatgpt_plus",
    "canva": "canva", "grammarly": "grammarly", "linkedin": "linkedin_premium",
    "x premium": "x_premium", "twitter": "x_premium",
    "patreon": "patreon", "twitch": "twitch", "crunchyroll": "crunchyroll",
    "peacock": "peacock", "paramount": "paramount_plus", "max.com": "max_hbo",
    "espn": "espn_plus", "peloton": "peloton", "strava": "strava",
    "duolingo": "duolingo", "babbel": "babbel", "headspace": "headspace",
    "calm": "calm", "masterclass": "masterclass", "coursera": "coursera",
    "nytimes": "nyt", "new york times": "nyt", "wsj": "wsj",
    "wall street journal": "wsj", "athletic": "the_athletic",
    "doordash": "doordash", "dashpass": "doordash", "uber one": "uber_one",
    "instacart": "instacart", "hellofresh": "hellofresh",
    "planet fitness": "planet_fitness", "goodlife": "goodlife",
    "crunch fitness": "crunch", "costco": "costco",
}

CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP"}

AMOUNT_RE = re.compile(
    r"(?P<sym>[$€£])\s?(?P<amt>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})?)"
    r"|(?P<cur>USD|CAD|EUR|GBP)\s?(?P<amt2>\d+(?:\.\d{2})?)",
    re.IGNORECASE,
)

FREQUENCY_HINTS = {
    "monthly": ["per month", "/month", "monthly", "every month", "billed monthly"],
    "yearly": ["per year", "/year", "yearly", "annual", "annually", "billed annually", "billed yearly"],
}


def is_receipt_candidate(subject: str, snippet: str, sender: str) -> bool:
    """Heuristic: does this email look like a payment receipt/invoice?"""
    text = f"{subject} {snippet} {sender}".lower()
    return any(kw in text for kw in RECEIPT_KEYWORDS)


def normalise_merchant(sender: str, subject: str) -> tuple[str, str]:
    """Return (display_name, merchant_key). Unknown senders get a stable key."""
    text = f"{sender} {subject}".lower()
    for alias, key in MERCHANT_ALIASES.items():
        if alias in text:
            return key.replace("_", " ").title().replace(" Plus", "+"), key
    # Fall back to a cleaned sender-domain-based name.
    m = re.search(r"@([\w.-]+)", sender or "")
    domain = (m.group(1).lower() if m else (sender or "unknown"))[:60]
    base = re.sub(r"^(www|mail|no-?reply|noreply|billing|support|accounts?)[.-]?", "", domain)
    base = base.split(".")[0].replace("-", " ").strip() or "unknown"
    key = re.sub(r"[^a-z0-9]+", "_", base.lower())[:40].strip("_") or "unknown"
    return base.title(), f"merchant_{key}"


def extract_amount(text: str) -> tuple[float | None, str]:
    """Extract the first plausible billed amount from text. Returns (amount, currency)."""
    for m in AMOUNT_RE.finditer(text or ""):
        sym, amt, cur, amt2 = m.group("sym"), m.group("amt"), m.group("cur"), m.group("amt2")
        try:
            if sym and amt:
                return float(amt.replace(",", "")), CURRENCY_SYMBOLS.get(sym, "USD")
            if cur and amt2:
                return float(amt2), cur.upper()
        except ValueError:
            continue
    return None, "USD"


def infer_frequency(text: str) -> str:
    low = (text or "").lower()
    for freq, hints in FREQUENCY_HINTS.items():
        if any(h in low for h in hints):
            return freq
    return "monthly"  # receipts without an explicit period are usually monthly


def score_confidence(occurrences: int, has_amount: bool, keyword_hits: int) -> float:
    """0.0-1.0 heuristic confidence that this is a real recurring subscription."""
    score = 0.3
    if occurrences >= 3:
        score += 0.4
    elif occurrences >= 2:
        score += 0.25
    if has_amount:
        score += 0.15
    score += min(0.15, 0.05 * keyword_hits)
    return round(min(1.0, score), 2)


def detect_recurring(receipts: list[dict]) -> list[dict]:
    """Group receipts by merchant and flag recurring patterns.

    Each receipt dict: {subject, sender, snippet, body, date (YYYY-MM-DD)}.
    A merchant with >=2 receipts spanning >=20 days (or same amount twice)
    is treated as a recurring subscription.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    display: dict[str, str] = {}
    for r in receipts:
        name, key = normalise_merchant(r.get("sender", ""), r.get("subject", ""))
        _, currency = extract_amount(f"{r.get('subject','')} {r.get('snippet','')} {r.get('body','')}")
        key = f"{key}_{currency.lower()}"
        if r not in groups[key]:
            groups[key].append(r)
        display[key] = name

    out = []
    for key, items in groups.items():
        dates = sorted(i.get("date", "") for i in items if i.get("date"))
        amounts = [extract_amount(f"{i.get('subject','')} {i.get('snippet','')} {i.get('body','')}")
                   for i in items]
        billed = [a for a, _ in amounts if a is not None]
        span_days = 0
        if len(dates) >= 2:
            try:
                d0 = date.fromisoformat(dates[0])
                d1 = date.fromisoformat(dates[-1])
                span_days = (d1 - d0).days
            except ValueError:
                span_days = 0
        recurring = len(set(dates)) >= 2 and span_days >= 20

        freq_counts: dict[str, int] = defaultdict(int)
        for i in items:
            freq_counts[infer_frequency(f"{i.get('subject','')} {i.get('body','')}")] += 1
        frequency = max(freq_counts, key=freq_counts.get) if freq_counts else "monthly"

        latest = max(items, key=lambda item: item.get("date", ""))
        amount, currency = extract_amount(f"{latest.get('subject','')} {latest.get('snippet','')} {latest.get('body','')}")
        keyword_hits = sum(
            1 for i in items
            if is_receipt_candidate(i.get("subject", ""), i.get("snippet", ""), i.get("sender", ""))
        )
        out.append({
            "merchant": display[key],
            "merchant_key": key,
            "amount": amount,
            "currency": currency,
            "frequency": frequency,
            "occurrences": len(items),
            "first_seen": dates[0] if dates else "",
            "last_seen": dates[-1] if dates else "",
            "recurring": recurring,
            "confidence": score_confidence(len(items), amount is not None, keyword_hits)
                          if recurring else round(score_confidence(len(items), amount is not None, keyword_hits) * 0.5, 2),
        })
    # Recurring first, then by confidence.
    out.sort(key=lambda s: (not s["recurring"], -s["confidence"]))
    return out
