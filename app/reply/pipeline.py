"""The one application entry point for creating a safe pending draft."""

from dataclasses import dataclass
from pathlib import Path

from app.db.drafts import create_draft_reply
from app.db.faqs import FAQEntry, list_approved_faqs
from app.providers.llm_base import LLMProvider

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
    email: object, database_path: Path, llm: LLMProvider, threshold: float | None = None
) -> DraftResult:
    """Guard, match approved FAQs, validate the rewrite, and store no escalation."""
    escalation = check_escalation(email)
    if escalation:
        return DraftResult("escalated", reason=escalation.reason)
    # Keep this explicit as a second safety boundary: matching calls embed().
    # An unclear message must never reach any provider method.
    if not is_meaningful_message(email):
        return DraftResult("escalated", reason="email is empty or unclear")
    match = find_best_faq(email, list_approved_faqs(database_path), llm)
    limit = configured_threshold() if threshold is None else threshold
    if match.faq is None or match.score < limit:
        return DraftResult("escalated", "no sufficiently confident approved FAQ match", match=match)
    try:
        result = draft_reply(email, match.faq, llm)
    except (ValueError, TypeError, KeyError, RuntimeError):
        return DraftResult("escalated", "invalid or unsafe provider reply", faq=match.faq, match=match)
    email_id = getattr(email, "id", None)
    if email_id is None:
        return DraftResult("escalated", "email has no database id", faq=match.faq, match=match)
    draft_id = create_draft_reply(database_path, int(email_id), result.reply, faq_id=result.faq_id)
    return DraftResult("pending", draft_id=draft_id, faq=match.faq, match=match, body=result.reply)
