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
                   subject, body, received_at
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
        )
        for row in rows
    ]
