"""Explainable desktop inbox triage; no Gmail writes or sending capability."""
from dataclasses import dataclass
import re

from app.db import automation as store
from app.db.connections import mail_paused
from app.providers.llm_fake import FakeLLMProvider
from app.reply.guardrails import check_escalation
from app.reply.pipeline import prepare_reply, DraftResult


@dataclass(frozen=True)
class AutomationResult:
    processed: int = 0
    drafts: int = 0
    attention: int = 0
    paused: bool = False


def classify(email):
    text = f"{email.subject}\n{email.body}"
    patterns = (
        ("complaint", r"\b(refund\w*|complaint|complain\w*|angry|unacceptable|furious|outrageous|lawyer|legal|lawsuit|sue|attorney)\b"),
        ("quote", r"\b(quote|quotation|estimate|pricing)\b"),
        ("billing", r"\b(invoice\w*|billing|payment\w*|charged|receipt|money|price\w*|cost\w*|amount)\b|[$€£]"),
        ("question", r"\?|\b(question|opening|hours|delivery|shipping|where|when|how|what)\b"),
    )
    for category, pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            # Record the category/rule, never copy arbitrary customer text into logs.
            return category, f"Matched the {category} keyword rule."
    return "other", "No category keyword matched; used other."


def run_automation(database_path, llm=None, *, limit=50, mailbox=None, draft_replies=True):
    config = store.settings(database_path)
    if not config["enabled"] or mail_paused(database_path):
        return AutomationResult(paused=True)
    if not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError("Automation batch size must be between 1 and 500.")
    processed = drafts = attention = 0
    llm = llm or FakeLLMProvider()
    for email in store.candidates(database_path, limit, mailbox):
        current = store.settings(database_path)
        if not current["enabled"] or current["revision"] != config["revision"] or mail_paused(database_path):
            break
        category, reason = classify(email)
        escalation = check_escalation(email)
        priority = "high" if escalation or re.search(r"\b(urgent|asap|immediately)\b", email.subject + "\n" + email.body, re.I) else "normal"
        if escalation:
            reason += " " + escalation.reason + "; human review required."
        customer_id = store.matching_customer(database_path, email)
        result = None
        if draft_replies and customer_id is not None and config["auto_draft"] and not store.has_reply(database_path, email.id):
            if escalation:
                result = DraftResult("escalated", escalation.reason)
            else:
                try:
                    result = prepare_reply(email, database_path, llm)
                except Exception:
                    result = DraftResult("escalated", "Reply suggestion failed; prepare a reply manually.")
        saved = store.record_result(database_path, email, customer_id, category, priority, reason, result, config["revision"])
        if saved:
            processed += 1
            drafts += saved["outcome"] == "review"
            attention += saved["outcome"] in {"needs_attention", "needs_assignment"}
    return AutomationResult(processed, drafts, attention)
