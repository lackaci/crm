"""Deterministic matching of an email to an approved FAQ."""

import math
import os
from dataclasses import dataclass
from typing import Sequence

from app.db.faqs import FAQEntry
from app.providers.llm_base import LLMProvider

DEFAULT_THRESHOLD = 0.75


@dataclass(frozen=True)
class MatchResult:
    faq: FAQEntry | None
    score: float


def configured_threshold() -> float:
    """Read the threshold without making a bad environment value unsafe."""
    try:
        value = float(os.getenv("REPLY_MATCH_THRESHOLD", DEFAULT_THRESHOLD))
    except ValueError:
        return DEFAULT_THRESHOLD
    return min(1.0, max(0.0, value))


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Return cosine similarity; vectors with no direction do not match."""
    if len(left) != len(right) or not left or not right:
        return 0.0
    product = sum(a * b for a, b in zip(left, right))
    sizes = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return product / sizes if sizes else 0.0


def find_best_faq(email: object, faqs: Sequence[FAQEntry], llm: LLMProvider) -> MatchResult:
    """Embed the message and approved questions, using FAQ order as tie-breaker."""
    if not faqs:
        return MatchResult(None, 0.0)
    text = f"{getattr(email, 'subject', '')}\n{getattr(email, 'body', '')}"
    email_vector = llm.embed(text)
    scored = ((faq, cosine_similarity(email_vector, llm.embed(faq.question))) for faq in faqs)
    faq, score = max(scored, key=lambda item: item[1])
    return MatchResult(faq, score)
