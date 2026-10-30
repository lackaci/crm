"""The one application entry point for creating a safe pending draft."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.db.drafts import create_draft_reply
from app.db.faqs import FAQEntry, list_approved_faqs
from app.providers.llm_base import LLMProvider
from app.providers.llm_masked import MaskedLLMProvider

from .drafter import draft_reply
from .guardrails import check_escalation, is_meaningful_message
from .matcher import MatchResult, configured_threshold, find_best_faq


@dataclass(frozen=True)
class DraftResult:
    status: str
    reason: str | None = None
    draft_id: int | None = None
    faq: FAQEntry | None = None
    match: MatchResult | None = None
    body: str | None = None


def process_email(
    email: object, database_path: Path, llm: LLMProvider, threshold: float | None = None,
    audit_callback: Callable[[dict], None] | None = None,
    audit_database_path: Path | None = None,
) -> DraftResult:
    """Guard, match approved FAQs, validate the rewrite, and store no escalation."""
    escalation = check_escalation(email)
    if escalation:
        return DraftResult("escalated", reason=escalation.reason)
    # Keep this explicit as a second safety boundary: matching calls embed().
    # An unclear message must never reach any provider method.
    if not is_meaningful_message(email):
        return DraftResult("escalated", reason="email is empty or unclear")
    if not getattr(llm, "accepts_customer_data", False):
        return DraftResult(
            "escalated",
            reason="cloud customer-data drafting/matching is disabled before M10",
        )
    try:
        safe_llm = llm if isinstance(llm, MaskedLLMProvider) else MaskedLLMProvider(llm)
        match = find_best_faq(email, list_approved_faqs(database_path), safe_llm)
    except NotImplementedError:
        # The cloud completion boundary is M10-safe, but this adapter still
        # has no offline embedding endpoint for FAQ matching.
        if type(llm).__name__ == "CloudLLMProvider":
            return DraftResult(
                "escalated",
                reason="cloud customer-data drafting is disabled before M10",
            )
        return DraftResult(
            "escalated",
            reason="provider does not support the required offline embedding operation",
        )
    limit = configured_threshold() if threshold is None else threshold
    if match.faq is None or match.score < limit:
        return DraftResult("escalated", "no sufficiently confident approved FAQ match", match=match)
    try:
        if audit_callback is None and audit_database_path is not None:
            from app.providers.audit import database_audit_callback
            audit_callback = database_audit_callback(audit_database_path)
        result = draft_reply(email, match.faq, safe_llm, audit_callback=audit_callback)
    except (ValueError, TypeError, KeyError, RuntimeError):
        return DraftResult("escalated", "invalid or unsafe provider reply", faq=match.faq, match=match)
    email_id = getattr(email, "id", None)
    if email_id is None:
        return DraftResult("escalated", "email has no database id", faq=match.faq, match=match)
    draft_id = create_draft_reply(database_path, int(email_id), result.reply, faq_id=result.faq_id)
    return DraftResult("pending", draft_id=draft_id, faq=match.faq, match=match, body=result.reply)
