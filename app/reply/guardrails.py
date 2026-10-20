"""Conservative checks that run before any model call."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Escalation:
    reason: str


_RULES = (
    ("refund", re.compile(r"\brefund(?:s|ed|ing)?\b", re.I)),
    ("legal threat", re.compile(r"\b(?:sue|lawyer|legal|lawsuit|attorney)\b", re.I)),
    ("anger or complaint", re.compile(
        r"\b(?:angry|unacceptable|complaint|complain|furious|outrageous)\b", re.I
    )),
    ("money or price", re.compile(
        r"(?:[$\u20ac\u00a3]"
        r"|\b(?:usd|eur|euro|dollars?|euros?|forints?)\b"
        r"|\b\d+(?:[.,]\d+)?\s*(?:usd|eur|euro|dollars?|forints?)\b"
        r"|\b(?:money|price|prices|cost|costs|how much|amount)\b)", re.I
    )),
)


def is_meaningful_message(email: object) -> bool:
    """Return whether the email contains at least one real, readable word.

    This deliberately does not try to understand the message.  It only
    prevents punctuation, whitespace, and number-only input from reaching a
    provider (including the embedding provider).
    """
    subject = str(getattr(email, "subject", "") or "")
    body = str(getattr(email, "body", "") or "")
    words = re.findall(r"[^\W\d_]+", f"{subject}\n{body}", flags=re.UNICODE)
    return any(len(word) >= 2 for word in words)


def check_escalation(email: object) -> Escalation | None:
    """Return an explainable reason, or None when the message is safe."""
    subject = str(getattr(email, "subject", "") or "")
    body = str(getattr(email, "body", "") or "")
    text = f"{subject}\n{body}".strip()
    if not text or not is_meaningful_message(email):
        return Escalation("email is empty or unclear")
    for reason, rule in _RULES:
        if rule.search(text):
            return Escalation(f"contains {reason}")
    return None
