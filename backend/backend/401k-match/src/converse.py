"""Conversational intake parsing for the 401(k) Match Finder golden path.

The agent is the UI: the connector asks one small question at a time and each
free-text answer is parsed here. All parsing is defensive — anything not
understood returns None so the connector can ask again in plain language.
"""

import re

AFFIRMATIVE = {
    "yes", "y", "yeah", "yep", "sure", "ok", "okay", "sounds good",
    "use that", "go with that", "default", "that works", "fine",
}


def parse_money(text: str) -> float | None:
    """Extract an annual salary from free text: '$120k', '120k', '120,000', '$85,000'."""
    t = re.sub(r"\d+(?:\.\d+)?\s*%", " ", text)  # remove percent tokens first
    m = re.search(r"\$?\s*([\d,]{2,}(?:\.\d+)?)\s*([kK])?\b", t)
    if not m:
        return None
    try:
        value = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    if m.group(2):
        value *= 1000
    if not 1_000 <= value <= 10_000_000:
        return None
    return value


def parse_pct(text: str) -> float | None:
    """Extract a contribution percentage: '4%', '4 percent', 'contribution is 4'."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if m:
        value = float(m.group(1))
    else:
        m = re.search(r"(\d+(?:\.\d+)?)\s*percent", text, re.I)
        if m:
            value = float(m.group(1))
        else:
            m = re.search(r"contribut\w*\s*(?:is|of|at|:)?\s*(\d+(?:\.\d+)?)", text, re.I)
            if not m:
                return None
            value = float(m.group(1))
    if not 0 <= value <= 100:
        return None
    return value


def is_affirmative(text: str) -> bool:
    t = text.strip().lower().rstrip(".,!")
    return t in AFFIRMATIVE


def parse_match(text: str) -> tuple[float, float] | None:
    """Extract a match formula -> (match_pct, match_cap_pct).

    Understands: 'yes' (default 50/6), '100% up to 4%', 'dollar for dollar up
    to 3%', '50 cents on the dollar up to 6%'.
    """
    if is_affirmative(text):
        return (50.0, 6.0)
    t = text.lower()
    cap = None
    mcap = re.search(r"up to\s*(\d+(?:\.\d+)?)\s*%?", t)
    if mcap:
        cap = float(mcap.group(1))
    if "dollar for dollar" in t or "100%" in t or "100 percent" in t:
        rate = 100.0
    elif "50 cents" in t or "fifty cents" in t:
        rate = 50.0
    else:
        mrate = re.search(r"(\d+(?:\.\d+)?)\s*%?\s*(?:match|matching)?\s*up to", t)
        if not mrate:
            return None
        rate = float(mrate.group(1))
    if cap is None or not 0 < rate <= 200 or not 0 < cap <= 100:
        return None
    return (rate, cap)
