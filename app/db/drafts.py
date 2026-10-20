"""SQLite persistence helpers for generated reply drafts."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .database import open_database

DraftStatus = Literal["pending", "approved", "rejected", "escalated"]
VALID_DRAFT_STATUSES = frozenset(("pending", "approved", "rejected", "escalated"))


@dataclass(frozen=True)
class DraftReply:
    """A reply draft associated with an incoming email."""

    id: int
    email_id: int
    faq_id: int | None
    status: DraftStatus
    body: str
    created_at: str


def create_draft_reply(
    database_path: Path,
    email_id: int,
    body: str,
    faq_id: int | None = None,
    actor: str = "reply-pipeline",
) -> int:
    """Store a pending draft, optionally using an approved FAQ."""
    body = body.strip()
    if not body:
        raise ValueError("Draft body is required.")

    with open_database(database_path) as connection:
        if faq_id is not None:
            faq = connection.execute(
                "SELECT approved FROM faq_entries WHERE id = ?", (faq_id,)
            ).fetchone()
            if faq is None:
                raise ValueError("FAQ was not found.")
            if not bool(faq["approved"]):
                raise ValueError("Only approved FAQs can be linked to a draft.")

        cursor = connection.execute(
            """
            INSERT INTO draft_replies (email_id, faq_id, body)
            VALUES (?, ?, ?)
            """,
            (email_id, faq_id, body),
        )
        connection.execute(
            "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
            (actor, "draft_created", f"draft_id={cursor.lastrowid}"),
        )
        return int(cursor.lastrowid)


def create_draft(
    database_path: Path,
    email_id: int,
    body: str,
    faq_id: int | None = None,
    actor: str = "reply-pipeline",
) -> int:
    """Short alias for :func:`create_draft_reply`."""
    return create_draft_reply(database_path, email_id, body, faq_id, actor)


def save_draft(
    database_path: Path,
    email_id: int,
    body: str,
    faq_id: int | None = None,
    actor: str = "reply-pipeline",
) -> int:
    """Application-facing name for creating a reply draft."""
    return create_draft_reply(database_path, email_id, body, faq_id, actor)


def get_draft(database_path: Path, draft_id: int) -> DraftReply | None:
    """Return one draft, or ``None`` when the ID does not exist."""
    with open_database(database_path) as connection:
        row = connection.execute(
            """
            SELECT id, email_id, faq_id, status, body, created_at
            FROM draft_replies
            WHERE id = ?
            """,
            (draft_id,),
        ).fetchone()
    return _draft_from_row(row) if row is not None else None


def list_drafts(database_path: Path, email_id: int | None = None) -> list[DraftReply]:
    """Return drafts in creation order, optionally for one email."""
    with open_database(database_path) as connection:
        if email_id is None:
            rows = connection.execute(
                """
                SELECT id, email_id, faq_id, status, body, created_at
                FROM draft_replies ORDER BY id
                """
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT id, email_id, faq_id, status, body, created_at
                FROM draft_replies WHERE email_id = ? ORDER BY id
                """,
                (email_id,),
            ).fetchall()
    return [_draft_from_row(row) for row in rows]


def set_draft_status(
    database_path: Path,
    draft_id: int,
    status: DraftStatus | str,
    actor: str = "reply-pipeline",
) -> None:
    """Set a schema-supported status and record the change in the audit log."""
    if status not in VALID_DRAFT_STATUSES:
        raise ValueError(f"Invalid draft status: {status!r}.")

    with open_database(database_path) as connection:
        cursor = connection.execute(
            "UPDATE draft_replies SET status = ? WHERE id = ?",
            (status, draft_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("Draft was not found.")
        connection.execute(
            "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
            (actor, "draft_status_changed", f"draft_id={draft_id}; status={status}"),
        )


def update_draft_status(
    database_path: Path,
    draft_id: int,
    status: DraftStatus | str,
    actor: str = "reply-pipeline",
) -> None:
    """Alias for :func:`set_draft_status`."""
    set_draft_status(database_path, draft_id, status, actor)


def _draft_from_row(row: object) -> DraftReply:
    """Convert a SQLite row to the public draft dataclass."""
    return DraftReply(
        id=int(row["id"]),  # type: ignore[index]
        email_id=int(row["email_id"]),  # type: ignore[index]
        faq_id=int(row["faq_id"]) if row["faq_id"] is not None else None,  # type: ignore[index]
        status=str(row["status"]),  # type: ignore[index]
        body=str(row["body"]),  # type: ignore[index]
        created_at=str(row["created_at"]),  # type: ignore[index]
    )
