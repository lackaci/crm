"""Prompt construction and strict validation of model output."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.db.faqs import FAQEntry
from app.providers.llm_base import CustomerDataNotAllowedError, LLMProvider
from app.providers.llm_masked import MaskedLLMProvider


@dataclass(frozen=True)
class ReplyDraft:
    faq_id: int
    reply: str


PROMPT_VERSION = "reply_v1"
PROMPT_PATH = Path(__file__).parents[1] / "prompts" / f"{PROMPT_VERSION}.txt"


def draft_reply(email: object, faq: FAQEntry, llm: LLMProvider,
                audit_callback: Callable[[dict], None] | None = None) -> ReplyDraft:
    """Ask only for a rewrite of the selected approved answer."""
    if not getattr(llm, "accepts_customer_data", False):
        raise CustomerDataNotAllowedError(
            "customer-bearing cloud drafting is disabled before M10"
        )
    lines = PROMPT_PATH.read_text(encoding="utf-8").splitlines()
    system = "\n".join(lines[:3])
    prompt = (
        f"FAQ_ID: {faq.id}\nAPPROVED_ANSWER: {faq.answer}\n"
        f"CUSTOMER_NAME: {getattr(email, 'from_name', '')}\n"
        f"EMAIL_CONTEXT: subject={getattr(email, 'subject', '')}; "
        f"body={getattr(email, 'body', '')}\n"
        + lines[3]
    )
    # Give providers that expose an audit sink the pipeline sink before the
    # invocation.  This lets cloud failures audit too; a post-call fallback
    # would miss failures and could duplicate successful cloud records.
    provider_has_audit_sink = hasattr(llm, "audit_callback")
    if audit_callback and provider_has_audit_sink:
        llm.audit_callback = audit_callback
    safe_llm = llm if isinstance(llm, MaskedLLMProvider) else MaskedLLMProvider(llm)
    raw = safe_llm.complete(system, prompt, json_mode=True)
    # Keep the fallback for simple providers (including test doubles) without
    # ever recording prompt or customer content.
    if audit_callback and not provider_has_audit_sink:
        audit_callback({
            "provider": type(llm).__name__.removesuffix("LLMProvider").lower(),
            "model": getattr(llm, "model", type(llm).__name__),
            "prompt_version": PROMPT_VERSION, "faq_id": faq.id,
            "prompt_tokens": None, "completion_tokens": None,
            "total_tokens": None, "cost": None,
        })
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
