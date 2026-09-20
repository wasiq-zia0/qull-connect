"""Sanitize user-supplied text before it is rendered anywhere.

All intake text is untrusted data: it must never alter queries, PDF layout,
or instructions. reportlab draws literal text (no HTML/markup interpretation),
but control characters, runaway newlines, and extreme lengths can still break
layout or smuggle content. This module strips control chars, collapses
whitespace, and enforces length caps.
"""
import re

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS = re.compile(r"[ \t\u00a0]+")

DEFAULT_MAX = 500


def clean_text(value: object, max_len: int = DEFAULT_MAX) -> str:
    if value is None:
        return ""
    text = str(value)
    text = _CONTROL.sub("", text)
    # keep single newlines as paragraph breaks; collapse everything else
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    lines = [ln for ln in lines if ln]
    collapsed: list[str] = []
    for ln in lines:
        ln = _WS.sub(" ", ln)
        if len(collapsed) < 12:  # cap line count to bound PDF growth
            collapsed.append(ln)
    text = "\n".join(collapsed)
    return text[:max_len]


def money(cents: int) -> str:
    return f"${cents / 100:,.2f}"
