"""Application-level read-only email synchronization."""

from dataclasses import dataclass
from pathlib import Path

from app.db.emails import import_emails
from app.providers.email_base import EmailProvider
from app.providers.audit import database_sync_audit_callback


@dataclass(frozen=True)
class SyncResult:
    imported: int
    error: str | None = None
    automation: object | None = None
    automation_error: bool = False
    existing: int = 0
    failed: int = 0
    remaining: int = 0


def synchronize_email(
    database_path: Path,
    provider: EmailProvider,
    audit_callback=None,
    storage_root: Path | None = None,
    llm_provider=None,
    progress=None,
) -> SyncResult:
    """Fetch and import messages, returning a plain-language failure."""
    audit = audit_callback or database_sync_audit_callback(database_path)
    provider_name = getattr(
        provider, "provider_name", provider.__class__.__name__.replace("Provider", "").lower()
    )

    def record(operation: str, outcome: str, count: int = 0, category: str | None = None):
        metadata = {
            "provider": provider_name,
            "operation": operation,
            "count": count,
            "outcome": outcome,
        }
        if category:
            metadata["error_category"] = category
        try:
            audit(metadata)
        except Exception:
            # Auditing must not turn a successful, already-imported sync into a failure.
            pass

    record("synchronize", "started")
    from app.db.connections import mail_paused
    if mail_paused(database_path):
        record("synchronize", "paused")
        return SyncResult(0, "Mail activity is paused. Resume it in Gmail settings.")
    try:
        partial = None
        if callable(getattr(provider, "import_incrementally", None)):
            partial = provider.import_incrementally(database_path, storage_root=storage_root, progress=progress)
            imported = partial.imported
        else:
            imported = import_emails(database_path, provider, storage_root=storage_root)
        record("synchronize", ("partial" if imported else "failure") if partial and partial.error else "success", imported)
    except Exception as error:
        category = "".join(
            character
            for character in error.__class__.__name__
            if character.isalnum() or character == "_"
        )[:64] or "unknown"
        record("synchronize", "failure", category=category)
        return SyncResult(
            imported=0,
            error="Email synchronization failed. Please try again.",
        )
    # Import is already committed. Triage failure must not misreport it or
    # cause a second fetch; unprocessed messages are retried on the next run.
    try:
        from app.db import email_workflow as workflow
        config = workflow.settings(database_path)
        if config["selected"]:
            from app.reply.email_assistant import DraftingResult
            from app.automation import run_automation
            mailbox = getattr(provider, "expected_mailbox", None)
            # Keep optional category/staff routing without invoking the legacy AI pipeline.
            run_automation(database_path, mailbox=mailbox, draft_replies=False)
            result = DraftingResult()
            if config["auto_draft"]:
                workflow.enqueue(database_path, mailbox=mailbox)
                result.error = config['ai_error'] if config['ai_paused'] else None
                # The desktop drains jobs in its own worker; slow AI must not
                # hold up the next Gmail check or the import's result display.
            import time
            workflow.update(database_path, last_check=time.time(), last_result=
                f"Imported {imported} new; {partial.existing if partial else 0} existing; {partial.failed if partial else 0} failed. "
                f"{result.drafts} replies prepared." + (" " + result.error if result.error else ""))
        else:
            from app.automation import run_automation
            result = run_automation(database_path, llm_provider, mailbox=getattr(provider, "expected_mailbox", None))
        return SyncResult(imported, error=partial.error if partial else None, automation=None if getattr(result, 'paused', False) else result,
                          existing=partial.existing if partial else 0, failed=partial.failed if partial else 0,
                          remaining=partial.remaining if partial else 0)
    except Exception:
        record("triage", "failure", category="automation_error")
        return SyncResult(imported, error=partial.error if partial else None, automation_error=True,
                          existing=partial.existing if partial else 0, failed=partial.failed if partial else 0,
                          remaining=partial.remaining if partial else 0)
