"""Regression tests for database startup migrations."""

import sqlite3

from app.db.database import initialize_database, open_database


def test_initialize_migrates_legacy_columns_before_creating_indexes(tmp_path):
    database_path = tmp_path / "legacy.db"
    with sqlite3.connect(database_path) as connection:
        # These tables represent a database created before provider and file
        # provenance columns were introduced.
        connection.executescript(
            """
            CREATE TABLE emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_name TEXT NOT NULL,
                from_email TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL
            );
            CREATE TABLE files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                file_type TEXT NOT NULL,
                path TEXT NOT NULL
            );
            """
        )

    initialize_database(database_path)

    with open_database(database_path) as connection:
        email_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(emails)")
        }
        file_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(files)")
        }
        indexes = {
            row["name"] for row in connection.execute("PRAGMA index_list(emails)")
        }
        file_indexes = {
            row["name"] for row in connection.execute("PRAGMA index_list(files)")
        }

    assert {
        "provider",
        "remote_message_id",
        "remote_thread_id",
        "labels",
    } <= email_columns
    assert {"source_email_id", "source_attachment_id"} <= file_columns
    assert "idx_emails_provider_remote" in indexes
    assert "idx_files_source_attachment" in file_indexes

    # Startup remains idempotent after the migrated schema and indexes exist.
    initialize_database(database_path)
