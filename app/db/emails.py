"""Plain SQLite persistence for messages supplied by an :class:`EmailProvider`."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.providers.email_base import EmailProvider

from .database import open_database


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
    database_path: Path, provider: EmailProvider, actor: str = "email-import"
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
            external_id = _external_id(raw_record)
            if external_id is not None:
                existing = connection.execute(
                    "SELECT 1 FROM emails WHERE external_id = ? LIMIT 1",
                    (external_id,),
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
                    (customer_id, external_id, from_name, from_email,
                     subject, body, received_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    customer_id,
                    external_id,
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
    return imported


def _log_action(connection: Any, actor: str, action: str, details: str) -> None:
    """Insert an audit row on the import transaction's existing connection."""
    connection.execute(
        "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
        (actor, action, details),
    )


def list_emails_for_customer(
    database_path: Path, customer_id: int
) -> list[EmailRecord]:
    """Return messages assigned to a customer, oldest first."""
    with open_database(database_path) as connection:
        rows = connection.execute(
            """
            SELECT id, customer_id, external_id, from_name, from_email,
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
        )
        for row in rows
    ]


def get_email(database_path: Path, email_id: int) -> EmailRecord | None:
    """Return one stored email by ID for screens that show a related draft."""
    with open_database(database_path) as connection:
        row = connection.execute(
            """
            SELECT id, customer_id, external_id, from_name, from_email,
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
