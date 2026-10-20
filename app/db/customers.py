"""Database operations for customers.

The UI calls these functions instead of writing SQL directly.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .database import open_database


@dataclass(frozen=True)
class Customer:
    """A customer record shown by the customer screen."""

    id: int
    name: str
    email: str
    phone: str
    notes: str


def _validate_customer(name: str, email: str) -> tuple[str, str]:
    clean_name = name.strip()
    clean_email = email.strip()
    if not clean_name:
        raise ValueError("Customer name is required.")
    if not clean_email:
        raise ValueError("Customer email is required.")
    return clean_name, clean_email


def add_customer(
    database_path: Path, name: str, email: str, phone: str = "", notes: str = ""
) -> int:
    """Insert a customer and return its new ID."""
    clean_name, clean_email = _validate_customer(name, email)
    with open_database(database_path) as connection:
        cursor = connection.execute(
            "INSERT INTO customers (name, email, phone, notes) VALUES (?, ?, ?, ?)",
            (clean_name, clean_email, phone.strip(), notes.strip()),
        )
        return int(cursor.lastrowid)


def update_customer(
    database_path: Path,
    customer_id: int,
    name: str,
    email: str,
    phone: str = "",
    notes: str = "",
) -> None:
    """Update one existing customer."""
    clean_name, clean_email = _validate_customer(name, email)
    with open_database(database_path) as connection:
        cursor = connection.execute(
            """
            UPDATE customers
            SET name = ?, email = ?, phone = ?, notes = ?
            WHERE id = ?
            """,
            (clean_name, clean_email, phone.strip(), notes.strip(), customer_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("Customer was not found.")


def list_customers(database_path: Path) -> list[Customer]:
    """Return customers in a stable, easy-to-display order."""
    with open_database(database_path) as connection:
        rows = connection.execute(
            "SELECT id, name, email, phone, notes FROM customers ORDER BY name, id"
        ).fetchall()
    return [
        Customer(
            id=int(row["id"]),
            name=str(row["name"]),
            email=str(row["email"]),
            phone=str(row["phone"] or ""),
            notes=str(row["notes"] or ""),
        )
        for row in rows
    ]
