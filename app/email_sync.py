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


def synchronize_email(
    database_path: Path,
    provider: EmailProvider,
    audit_callback=None,
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
    try:
        imported = import_emails(database_path, provider)
        record("synchronize", "success", imported)
        return SyncResult(imported=imported)
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
