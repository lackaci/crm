"""Small SQLite helpers shared by the application."""

import sqlite3
from pathlib import Path


def initialize_database(database_path: Path) -> None:
    """Create the database tables if they do not exist yet."""
    schema_path = Path(__file__).with_name("schema.sql")
    with sqlite3.connect(database_path) as connection:
        connection.executescript(schema_path.read_text(encoding="utf-8"))
        document_columns = {row[1] for row in connection.execute("PRAGMA table_info(desktop_document_settings)")}
        for name, definition in {
            # Preserve the existing local choice during upgrade, especially
            # when an owner previously enabled automatic document processing.
            "provider": "TEXT NOT NULL DEFAULT 'local'",
            "online_model": "TEXT NOT NULL DEFAULT 'gpt-5.4-mini-2026-03-17'",
            "online_endpoint": "TEXT NOT NULL DEFAULT 'https://api.openai.com/v1/responses'",
            "api_key_env": "TEXT NOT NULL DEFAULT 'OPENAI_API_KEY'",
            "embedding_model": "TEXT NOT NULL DEFAULT 'text-embedding-3-small'",
            "online_replies": "INTEGER NOT NULL DEFAULT 0",
        }.items():
            if name not in document_columns:
                connection.execute(f"ALTER TABLE desktop_document_settings ADD COLUMN {name} {definition}")
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(emails)").fetchall()
        }
        if "status" not in columns:
            connection.execute(
                "ALTER TABLE emails ADD COLUMN status TEXT NOT NULL DEFAULT 'new'"
            )
        migrations = {
            "customer_id": "INTEGER REFERENCES customers(id)",
            "external_id": "TEXT",
            "received_at": "TEXT",
            "source_mailbox": "TEXT",
            "provider": "TEXT NOT NULL DEFAULT 'mock'",
            "remote_message_id": "TEXT",
            "remote_thread_id": "TEXT",
            "labels": "TEXT NOT NULL DEFAULT '[]'",
        }
        for name, definition in migrations.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE emails ADD COLUMN {name} {definition}")
        from app.db.inbox import migrate_inbox
        migrate_inbox(connection)
        from app.db.email_workflow import migrate as migrate_email_workflow
        migrate_email_workflow(connection)
        file_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(files)").fetchall()
        }
        for name, definition in {
            "source_email_id": "INTEGER REFERENCES emails(id)",
            "source_attachment_id": "TEXT",
        }.items():
            if name not in file_columns:
                connection.execute(f"ALTER TABLE files ADD COLUMN {name} {definition}")
        # Migrate the original FAQ shape. Existing knowledge was already
        # trusted by the application, so retain it as approved knowledge.
        faq_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(faq_entries)")
        }
        if "status" not in faq_columns:
            connection.execute(
                "ALTER TABLE faq_entries ADD COLUMN status "
                "TEXT NOT NULL DEFAULT 'draft'"
            )
        if "approved_at" not in faq_columns:
            connection.execute("ALTER TABLE faq_entries ADD COLUMN approved_at TEXT")
        # SQLite ALTER TABLE only permits constant defaults.
        if "updated_at" not in faq_columns:
            connection.execute("ALTER TABLE faq_entries ADD COLUMN updated_at TEXT")
        connection.execute(
            "UPDATE faq_entries SET updated_at = COALESCE(updated_at, created_at) "
            "WHERE updated_at IS NULL"
        )
        if "status" not in faq_columns:
            connection.execute(
                "UPDATE faq_entries SET status = 'approved', approved = 1, "
                "approved_at = COALESCE(approved_at, created_at)"
            )
        # Indexes must be created after the migrations above.  In particular,
        # older databases do not have the provider column yet.
        connection.execute("DROP INDEX IF EXISTS idx_emails_provider_remote")
        connection.execute("CREATE UNIQUE INDEX idx_emails_provider_remote "
            "ON emails(provider, COALESCE(source_mailbox,''), remote_message_id) WHERE remote_message_id IS NOT NULL")
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


def log_action(database_path: Path, actor: str, action: str, details: str) -> None:
    """Record one audit event using a parameterized query."""
    with open_database(database_path) as connection:
        connection.execute(
            "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
            (actor, action, details),
        )
