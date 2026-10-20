"""Prompt construction and strict validation of model output."""

import json
import re
from dataclasses import dataclass

from app.db.faqs import FAQEntry
from app.providers.llm_base import LLMProvider


@dataclass(frozen=True)
class ReplyDraft:
    faq_id: int
    reply: str


def draft_reply(email: object, faq: FAQEntry, llm: LLMProvider) -> ReplyDraft:
    """Ask only for a rewrite of the selected approved answer."""
    system = (
        "You rewrite customer replies safely. Use only the facts in APPROVED_ANSWER. "
        "Do not add prices, dates, links or promises. "
        "Return JSON: {faq_id, reply}."
    )
    prompt = (
        f"FAQ_ID: {faq.id}\nAPPROVED_ANSWER: {faq.answer}\n"
        f"CUSTOMER_NAME: {getattr(email, 'from_name', '')}\n"
        f"EMAIL_CONTEXT: subject={getattr(email, 'subject', '')}; "
        f"body={getattr(email, 'body', '')}\n"
        "Rewrite the approved answer only, greeting the customer by name."
    )
    raw = llm.complete(system, prompt, json_mode=True)
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError
        faq_id = int(value["faq_id"])
        reply = value["reply"]
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("provider returned invalid reply JSON") from exc
    if faq_id != faq.id or not isinstance(reply, str) or not reply.strip():
        raise ValueError("provider returned an invalid FAQ id or empty reply")
    if not _is_safe_rewrite(reply, faq.answer):
        raise ValueError("reply contains content outside the approved FAQ answer")
    if _unsafe_additions(reply, faq.answer):
        raise ValueError("reply adds numbers or URLs not present in the FAQ")
    return ReplyDraft(faq_id, reply.strip())


def _is_safe_rewrite(reply: str, answer: str) -> bool:
    """Allow only the answer itself, optionally preceded by a simple greeting.

    Whitespace is formatting, but any other words after the answer are
    substantive provider content and therefore fail closed.
    """
    actual = " ".join(reply.strip().split())
    approved = " ".join(answer.strip().split())
    if actual == approved:
        return True

    # The greeting is intentionally narrow; do not permit sign-offs or prose.
    greeting = re.compile(
        r"^(?:hello|hi|dear)\b(?:\s+[A-Za-z][A-Za-z .'-]*)?"
        r"\s*[,!:\-]\s*",
        re.IGNORECASE,
    )
    match = greeting.match(actual)
    return bool(match and actual[match.end():] == approved)


def _unsafe_additions(reply: str, answer: str) -> bool:
    numbers = re.findall(r"\b\d+(?:[.,]\d+)?\b", reply)
    urls = re.findall(r"https?://\S+|www\.\S+", reply, flags=re.I)
    return any(token not in answer for token in numbers + urls)
