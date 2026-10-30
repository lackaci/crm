"""Plain SQLite persistence for messages supplied by an :class:`EmailProvider`."""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from app.providers.email_base import EmailProvider

from .database import open_database
from .files import DEFAULT_STORAGE_ROOT


@dataclass(frozen=True)
class EmailRecord:
    """An incoming message, optionally associated with a CRM customer."""

    id: int
    customer_id: int | None
    external_id: str | None
    from_name: str
    from_email: str
    subject: str
    body: str
    received_at: str | None
    status: str = "new"
    provider: str = "mock"
    remote_message_id: str | None = None
    remote_thread_id: str | None = None
    labels: tuple[str, ...] = ()


def log_action(database_path: Path, actor: str, action: str, details: str) -> None:
    """Write one audit event for a database state change."""
    with open_database(database_path) as connection:
        connection.execute(
            "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
            (actor, action, details),
        )


def _text(record: dict[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Email field {key!r} is required.")
    return value.strip()


def _external_id(record: dict[str, Any]) -> str | None:
    """Use the provider id as the stable import key, when one is supplied."""
    value = record.get("external_id", record.get("id"))
    if value is None or value == "":
        return None
    return str(value)


def import_emails(
    database_path: Path,
    provider: EmailProvider,
    actor: str = "email-import",
    storage_root: Path | None = None,
) -> int:
    """Import provider messages and return the number of newly stored messages.

    A sender is matched case-insensitively to ``customers.email``.  Messages
    without a match are retained with a NULL ``customer_id``.
    """
    imported = 0
    records = provider.fetch_new_emails()
    with open_database(database_path) as connection:
        for raw_record in records:
            if not isinstance(raw_record, dict):
                raise ValueError("Email provider records must be mappings.")
            provider = str(raw_record.get("provider", "mock"))
            remote_message_id = raw_record.get("remote_message_id")
            external_id = _external_id(raw_record)
            if external_id is None and remote_message_id:
                external_id = str(remote_message_id)
            if external_id is not None:
                existing = connection.execute(
                    "SELECT 1 FROM emails WHERE "
                    "(provider = ? AND remote_message_id = ?) OR "
                    "(provider = ? AND external_id = ?) LIMIT 1",
                    (provider, str(remote_message_id) if remote_message_id else None,
                     provider, external_id),
                ).fetchone()
                if existing is not None:
                    continue

            from_email = _text(raw_record, "from_email")
            customer = connection.execute(
                "SELECT id FROM customers WHERE lower(email) = lower(?) LIMIT 1",
                (from_email,),
            ).fetchone()
            customer_id = int(customer["id"]) if customer is not None else None
            cursor = connection.execute(
                """
                INSERT INTO emails
                    (customer_id, external_id, provider, remote_message_id,
                     remote_thread_id, labels, from_name, from_email,
                     subject, body, received_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    customer_id,
                    external_id,
                    provider,
                    str(remote_message_id) if remote_message_id else None,
                    str(raw_record.get("remote_thread_id"))
                    if raw_record.get("remote_thread_id") else None,
                    json.dumps(raw_record.get("labels", [])),
                    _text(raw_record, "from_name"),
                    from_email,
                    _text(raw_record, "subject"),
                    _text(raw_record, "body"),
                    raw_record.get("received_at"),
                ),
            )
            _log_action(
                connection,
                actor,
                "email_added",
                f"email_id={cursor.lastrowid}; external_id={external_id or 'none'}",
            )
            imported += 1
            _import_attachments(
                connection, database_path, int(cursor.lastrowid), customer_id,
                raw_record.get("attachments", []), storage_root,
            )
    return imported


def _log_action(connection: Any, actor: str, action: str, details: str) -> None:
    """Insert an audit row on the import transaction's existing connection."""
    connection.execute(
        "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
        (actor, action, details),
    )


def _import_attachments(
    connection: Any,
    database_path: Path,
    email_id: int,
    customer_id: int | None,
    attachments: Any,
    storage_root: Path | None = None,
) -> None:
    """Write attachment bytes to the existing file store, never SQLite."""
    if customer_id is None or not isinstance(attachments, list):
        return
    root = Path(storage_root) if storage_root is not None else DEFAULT_STORAGE_ROOT
    folder = root / f"customer_{customer_id}"
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        filename = Path(str(attachment.get("filename", "attachment.bin"))).name
        content = attachment.get("content", b"")
        if isinstance(content, str):
            content = content.encode("utf-8")
        if not isinstance(content, bytes):
            continue
        source_id = str(attachment.get("attachment_id") or filename)
        exists = connection.execute(
            "SELECT 1 FROM files WHERE source_email_id = ? AND source_attachment_id = ?",
            (email_id, source_id),
        ).fetchone()
        if exists:
            continue
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / filename
        if destination.exists():
            destination = folder / f"{email_id}_{filename}"
        destination.write_bytes(content)
        connection.execute(
            """
            INSERT INTO files
              (customer_id, filename, file_type, path, source_email_id, source_attachment_id)
            VALUES (?, ?, 'other', ?, ?, ?)
            """,
            (customer_id, filename, destination.relative_to(root).as_posix(), email_id, source_id),
        )


def list_emails_for_customer(
    database_path: Path, customer_id: int
) -> list[EmailRecord]:
    """Return messages assigned to a customer, oldest first."""
    with open_database(database_path) as connection:
        rows = connection.execute(
            """
            SELECT id, customer_id, external_id, provider, remote_message_id,
                   remote_thread_id, labels, from_name, from_email,
                   subject, body, received_at, status
            FROM emails
            WHERE customer_id = ?
            ORDER BY received_at, id
            """,
            (customer_id,),
        ).fetchall()
    return [
        EmailRecord(
            id=int(row["id"]),
            customer_id=int(row["customer_id"]),
            external_id=str(row["external_id"])
            if row["external_id"] is not None
            else None,
            from_name=str(row["from_name"]),
            from_email=str(row["from_email"]),
            subject=str(row["subject"]),
            body=str(row["body"]),
            received_at=str(row["received_at"])
            if row["received_at"] is not None
            else None,
            status=str(row["status"]),
            provider=str(row["provider"]),
            remote_message_id=row["remote_message_id"],
            remote_thread_id=row["remote_thread_id"],
            labels=tuple(json.loads(row["labels"] or "[]")),
        )
        for row in rows
    ]


def list_unmatched_emails(database_path: Path) -> list[EmailRecord]:
    """Return imported messages that are not assigned to a CRM customer."""
    with open_database(database_path) as connection:
        rows = connection.execute(
            """
            SELECT id, customer_id, external_id, provider, remote_message_id,
                   remote_thread_id, labels, from_name, from_email,
                   subject, body, received_at, status
            FROM emails
            WHERE customer_id IS NULL
            ORDER BY received_at, id
            """
        ).fetchall()
    return [
        EmailRecord(
            id=int(row["id"]),
            customer_id=None,
            external_id=str(row["external_id"])
            if row["external_id"] is not None
            else None,
            from_name=str(row["from_name"]),
            from_email=str(row["from_email"]),
            subject=str(row["subject"]),
            body=str(row["body"]),
            received_at=str(row["received_at"])
            if row["received_at"] is not None
            else None,
            status=str(row["status"]),
            provider=str(row["provider"]),
            remote_message_id=row["remote_message_id"],
            remote_thread_id=row["remote_thread_id"],
            labels=tuple(json.loads(row["labels"] or "[]")),
        )
        for row in rows
    ]


def remove_gmail_emails_not_from(
    database_path: Path, allowed_senders: tuple[str, ...], actor: str = "email-cleanup"
) -> int:
    """Remove Gmail messages whose sender is not in the approved allowlist."""
    senders = tuple(sender.strip().casefold() for sender in allowed_senders if sender.strip())
    with open_database(database_path) as connection:
        if senders:
            placeholders = ",".join("?" for _ in senders)
            parameters: tuple[object, ...] = ("gmail", *senders)
            rows = connection.execute(
                f"""
                SELECT id FROM emails
                WHERE provider = ?
                  AND lower(from_email) NOT IN ({placeholders})
                """,
                parameters,
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT id FROM emails WHERE provider = 'gmail'"
            ).fetchall()
        email_ids = [int(row["id"]) for row in rows]
        if not email_ids:
            return 0
        placeholders = ",".join("?" for _ in email_ids)
        connection.execute(
            f"DELETE FROM files WHERE source_email_id IN ({placeholders})",
            email_ids,
        )
        connection.execute(
            f"DELETE FROM draft_replies WHERE email_id IN ({placeholders})",
            email_ids,
        )
        connection.execute(
            f"DELETE FROM emails WHERE id IN ({placeholders})",
            email_ids,
        )
        connection.execute(
            "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
            (actor, "gmail_cleanup", f"removed={len(email_ids)}"),
        )
        return len(email_ids)


def assign_email_to_customer(
    database_path: Path, email_id: int, customer_id: int, actor: str = "email-assignment"
) -> None:
    """Assign one unmatched email to an existing customer."""
    with open_database(database_path) as connection:
        customer = connection.execute(
            "SELECT id FROM customers WHERE id = ?", (customer_id,)
        ).fetchone()
        if customer is None:
            raise ValueError("Customer was not found.")
        email = connection.execute(
            "SELECT customer_id FROM emails WHERE id = ?", (email_id,)
        ).fetchone()
        if email is None:
            raise ValueError("Email was not found.")
        if email["customer_id"] is not None:
            raise ValueError("This email is already assigned to a customer.")
        connection.execute(
            "UPDATE emails SET customer_id = ? WHERE id = ?",
            (customer_id, email_id),
        )
        connection.execute(
            "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
            (actor, "email_assigned", f"email_id={email_id}; customer_id={customer_id}"),
        )


def get_email(database_path: Path, email_id: int) -> EmailRecord | None:
    """Return one stored email by ID for screens that show a related draft."""
    with open_database(database_path) as connection:
        row = connection.execute(
            """
            SELECT id, customer_id, external_id, provider, remote_message_id,
                   remote_thread_id, labels, from_name, from_email,
                   subject, body, received_at, status
            FROM emails
            WHERE id = ?
            """,
            (email_id,),
        ).fetchone()
    if row is None:
        return None
    return EmailRecord(
        id=int(row["id"]),
        customer_id=int(row["customer_id"]) if row["customer_id"] is not None else None,
        external_id=str(row["external_id"]) if row["external_id"] is not None else None,
        from_name=str(row["from_name"]),
        from_email=str(row["from_email"]),
        subject=str(row["subject"]),
        body=str(row["body"]),
        received_at=str(row["received_at"]) if row["received_at"] is not None else None,
        status=str(row["status"]),
        provider=str(row["provider"]),
        remote_message_id=row["remote_message_id"],
        remote_thread_id=row["remote_thread_id"],
        labels=tuple(json.loads(row["labels"] or "[]")),
    )


def set_email_status(database_path: Path, email_id: int, status: str) -> None:
    """Set the small workflow status shown in the inbox."""
    if status not in {"new", "seen", "done"}:
        raise ValueError("Email status must be new, seen, or done.")
    with open_database(database_path) as connection:
        cursor = connection.execute(
            "UPDATE emails SET status = ? WHERE id = ?", (status, email_id)
        )
        if cursor.rowcount != 1:
            raise ValueError("Email was not found.")


def mark_email_seen(database_path: Path, email_id: int) -> None:
    """Mark a new email as seen without changing completed emails."""
    with open_database(database_path) as connection:
        connection.execute(
            "UPDATE emails SET status = 'seen' WHERE id = ? AND status = 'new'",
            (email_id,),
        )


def customer_email_status(database_path: Path, customer_id: int) -> str:
    """Return the aggregate status displayed beside a customer."""
    with open_database(database_path) as connection:
        rows = connection.execute(
            "SELECT status FROM emails WHERE customer_id = ?", (customer_id,)
        ).fetchall()
    statuses = {str(row["status"]) for row in rows}
    if not statuses:
        return "No emails"
    if "new" in statuses:
        return "New"
    if statuses == {"done"}:
        return "Done"
    return "Seen"
