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
        migrations = {
            "provider": "TEXT NOT NULL DEFAULT 'mock'",
            "remote_message_id": "TEXT",
            "remote_thread_id": "TEXT",
            "labels": "TEXT NOT NULL DEFAULT '[]'",
        }
        for name, definition in migrations.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE emails ADD COLUMN {name} {definition}")
        file_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(files)").fetchall()
        }
        for name, definition in {
            "source_email_id": "INTEGER REFERENCES emails(id)",
            "source_attachment_id": "TEXT",
        }.items():
            if name not in file_columns:
                connection.execute(f"ALTER TABLE files ADD COLUMN {name} {definition}")
        # Indexes must be created after the migrations above.  In particular,
        # older databases do not have the provider column yet.
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_emails_provider_remote "
            "ON emails(provider, remote_message_id) WHERE remote_message_id IS NOT NULL"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_files_source_attachment "
            "ON files(source_email_id, source_attachment_id) "
            "WHERE source_email_id IS NOT NULL AND source_attachment_id IS NOT NULL"
        )


def open_database(database_path: Path) -> sqlite3.Connection:
    """Open a database connection with useful defaults for this app."""
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection
