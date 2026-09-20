"""Input sanitization.

ALL user-supplied text is untrusted data. Anything that gets rendered into
claim packs, nudges, or tool outputs passes through sanitize(): control
characters are stripped, length is capped, and HTML is escaped so rendered
content cannot alter markup or smuggle instructions.
"""
import html
import re

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize(value, max_len: int = 500) -> str:
    """Return a safe plain-text string for embedding in outputs."""
    if value is None:
        return ""
    s = _CONTROL.sub("", str(value)).strip()
    if len(s) > max_len:
        s = s[:max_len] + "…"
    return html.escape(s, quote=False)
