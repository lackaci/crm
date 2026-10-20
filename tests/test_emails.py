"""Focused tests for importing provider messages into SQLite."""

from pathlib import Path

from app.db.customers import add_customer
from app.db.database import initialize_database, open_database
from app.db.emails import import_emails, list_emails_for_customer
from app.providers.email_mock import MockEmailProvider


def _provider(records: list[dict[str, object]]) -> MockEmailProvider:
    class FixedProvider(MockEmailProvider):
        def fetch_new_emails(self) -> list[dict[str, object]]:
            return records

    return FixedProvider()


def test_import_persists_and_matches_sender(tmp_path: Path) -> None:
    database_path = tmp_path / "emails.db"
    initialize_database(database_path)
    customer_id = add_customer(database_path, "Anna Kovacs", "anna@example.com")

    assert import_emails(
        database_path,
        _provider(
            [
                {
                    "id": 7,
                    "from_name": "Anna Kovacs",
                    "from_email": "ANNA@example.com",
                    "subject": "Hello",
                    "body": "Message",
                }
            ]
        ),
    ) == 1

    messages = list_emails_for_customer(database_path, customer_id)
    assert len(messages) == 1
    assert messages[0].external_id == "7"
    assert messages[0].subject == "Hello"


def test_unmatched_sender_is_left_unassigned(tmp_path: Path) -> None:
    database_path = tmp_path / "emails.db"
    initialize_database(database_path)

    assert import_emails(
        database_path,
        _provider(
            [
                {
                    "id": 8,
                    "from_name": "Unknown",
                    "from_email": "unknown@example.com",
                    "subject": "Question",
                    "body": "Hi",
                }
            ]
        ),
    ) == 1

    with open_database(database_path) as connection:
        row = connection.execute(
            "SELECT customer_id FROM emails WHERE external_id = ?", ("8",)
        ).fetchone()
    assert row["customer_id"] is None


def test_repeated_import_does_not_duplicate_fixture_id(tmp_path: Path) -> None:
    database_path = tmp_path / "emails.db"
    initialize_database(database_path)
    add_customer(database_path, "Ada", "ada@example.com")
    provider = _provider(
        [
            {
                "id": 9,
                "from_name": "Ada",
                "from_email": "ada@example.com",
                "subject": "Once",
                "body": "Only once",
            }
        ]
    )

    assert import_emails(database_path, provider) == 1
    assert import_emails(database_path, provider) == 0

    with open_database(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM emails").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 1
