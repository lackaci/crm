"""Approval-queue operations for reply drafts.

This module deliberately keeps delivery behind the :class:`EmailProvider`
interface.  Editing and rejecting only change the local database; approval is
the one operation which may call a provider.
"""

from dataclasses import dataclass
from pathlib import Path
import sqlite3

from app.db.database import open_database
from app.db.drafts import DraftReply, _draft_from_row
from app.providers.email_base import EmailDeliveryError, EmailProvider


@dataclass(frozen=True)
class ApprovalResult:
    """The result of a successful delivery and approval."""

    draft: DraftReply
    delivery: str


def list_pending_drafts(database_path: Path) -> list[DraftReply]:
    """Return drafts currently waiting for a reviewer."""
    with open_database(database_path) as connection:
        rows = connection.execute(
            """
            SELECT id, email_id, faq_id, status, body, created_at
            FROM draft_replies
            WHERE status = ?
            ORDER BY id
            """,
            ("pending",),
        ).fetchall()
    return [_draft_from_row(row) for row in rows]


def edit_draft(
    database_path: Path,
    draft_id: int,
    body: str,
    actor: str = "reviewer",
) -> DraftReply:
    """Replace a pending draft body and record the edit."""
    _validate_id(draft_id)
    body = body.strip()
    if not body:
        raise ValueError("Draft body is required.")

    with open_database(database_path) as connection:
        _pending_row(connection, draft_id)
        connection.execute(
            "UPDATE draft_replies SET body = ? WHERE id = ?",
            (body, draft_id),
        )
        _audit(connection, actor, "draft_edited", f"draft_id={draft_id}")
        return _draft_from_row(
            connection.execute(
                """
                SELECT id, email_id, faq_id, status, body, created_at
                FROM draft_replies WHERE id = ?
                """,
                (draft_id,),
            ).fetchone()
        )


def reject_draft(
    database_path: Path, draft_id: int, actor: str = "reviewer"
) -> DraftReply:
    """Reject a pending draft without contacting the email provider."""
    _validate_id(draft_id)
    with open_database(database_path) as connection:
        _pending_row(connection, draft_id)
        connection.execute(
            "UPDATE draft_replies SET status = ? WHERE id = ?",
            ("rejected", draft_id),
        )
        _audit(connection, actor, "draft_rejected", f"draft_id={draft_id}")
        return _load_draft(connection, draft_id)


def approve_draft(
    database_path: Path,
    draft_id: int,
    provider: EmailProvider,
    actor: str = "reviewer",
) -> ApprovalResult:
    """Send one pending draft, then mark it approved.

    The provider is called before the status update.  Provider failures are
    audited and re-raised, so a failed delivery can never become approved.
    """
    _validate_id(draft_id)
    with open_database(database_path) as connection:
        row = _pending_row(connection, draft_id)
        recipient = str(row["from_email"])
        subject = str(row["subject"])
        body = str(row["body"])
        try:
            provider.send_email(recipient, f"Re: {subject}", body)
        except EmailDeliveryError as error:
            _audit(
                connection,
                actor,
                "draft_send_failed",
                f"draft_id={draft_id}; error={type(error).__name__}: {error}",
            )
            # The context manager would roll this audit back while re-raising.
            connection.commit()
            raise

        connection.execute(
            "UPDATE draft_replies SET status = ? WHERE id = ?",
            ("approved", draft_id),
        )
        _audit(
            connection,
            actor,
            "draft_approved",
            f"draft_id={draft_id}; sent/mock-sent",
        )
        connection.execute(
            "UPDATE emails SET status = 'done' WHERE id = ?", (int(row["email_id"]),)
        )
        return ApprovalResult(_load_draft(connection, draft_id), "sent/mock-sent")


def _pending_row(connection: sqlite3.Connection, draft_id: int) -> sqlite3.Row:
    """Fetch a pending draft with its recipient details or raise explicitly."""
    row = connection.execute(
        """
        SELECT d.id, d.email_id, d.faq_id, d.status, d.body, d.created_at,
               e.from_email, e.subject
        FROM draft_replies AS d
        JOIN emails AS e ON e.id = d.email_id
        WHERE d.id = ?
        """,
        (draft_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Draft was not found.")
    if str(row["status"]) != "pending":
        raise ValueError("Only pending drafts can be acted on.")
    return row


def _load_draft(connection: sqlite3.Connection, draft_id: int) -> DraftReply:
    row = connection.execute(
        """
        SELECT id, email_id, faq_id, status, body, created_at
        FROM draft_replies WHERE id = ?
        """,
        (draft_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Draft was not found.")
    return _draft_from_row(row)


def _audit(
    connection: sqlite3.Connection, actor: str, action: str, details: str
) -> None:
    connection.execute(
        "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
        (actor, action, details),
    )


def _validate_id(draft_id: int) -> None:
    if isinstance(draft_id, bool) or not isinstance(draft_id, int) or draft_id <= 0:
        raise ValueError("Draft ID must be a positive integer.")
