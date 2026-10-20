"""Deterministic matching of an email to an approved FAQ."""

import math
import os
import re
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


_MATCH_STOP_WORDS = {
    "a", "an", "and", "are", "can", "do", "does", "for", "how", "i",
    "is", "my", "on", "the", "to", "what", "when", "where", "you", "your",
    # Common question boilerplate is not useful for distinguishing FAQs.
    "day", "find", "help", "last", "long", "order", "take", "time", "until",
}


def _normalise_word(word: str) -> str:
    """Make a small, predictable singular/plural normalisation."""
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("s") and len(word) > 3:
        return word[:-1]
    return word


def _content_words(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {
        _normalise_word(word)
        for word in words
        if word not in _MATCH_STOP_WORDS
    }


def lexical_overlap(email_text: str, question: str) -> float:
    """Return overlap coefficient for content words.

    This asks whether all words in the smaller vocabulary occur in the
    larger one.  It works well for short customer subjects such as
    ``Delivery time`` while remaining explainable: no shared content word
    means zero.  Stop words are ignored so common words cannot create a
    match.
    """
    email_words = _content_words(email_text)
    question_words = _content_words(question)
    if not email_words or not question_words:
        return 0.0
    shared = email_words & question_words
    return len(shared) / min(len(email_words), len(question_words))


def find_best_faq(email: object, faqs: Sequence[FAQEntry], llm: LLMProvider) -> MatchResult:
    """Use the stronger of semantic and transparent lexical matching.

    The threshold stays at 0.75.  A lexical score is useful for short,
    perfectly descriptive subjects where a small fake/embedding model can
    under-score an otherwise obvious FAQ.  Taking the maximum is safe here
    because lexical matching only considers content-word overlap; unrelated
    text such as "change my account" gets zero.
    """
    if not faqs:
        return MatchResult(None, 0.0)
    text = f"{getattr(email, 'subject', '')}\n{getattr(email, 'body', '')}"
    email_vector = llm.embed(text)
    scored = (
        (
            faq,
            max(
                cosine_similarity(email_vector, llm.embed(faq.question)),
                lexical_overlap(text, faq.question),
            ),
        )
        for faq in faqs
    )
    faq, score = max(scored, key=lambda item: item[1])
    return MatchResult(faq, score)
