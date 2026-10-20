"""Small SQLite helpers shared by the application."""

import sqlite3
from pathlib import Path


def initialize_database(database_path: Path) -> None:
    """Create the database tables if they do not exist yet."""
    schema_path = Path(__file__).with_name("schema.sql")
    with sqlite3.connect(database_path) as connection:
        connection.executescript(schema_path.read_text(encoding="utf-8"))
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(emails)").fetchall()
        }
        if "status" not in columns:
            connection.execute(
                "ALTER TABLE emails ADD COLUMN status TEXT NOT NULL DEFAULT 'new'"
            )


def open_database(database_path: Path) -> sqlite3.Connection:
    """Open a database connection with useful defaults for this app."""
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection
