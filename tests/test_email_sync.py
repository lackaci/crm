"""Focused tests for safe synchronization errors and audit metadata."""

from app.db.database import initialize_database, open_database
from app.email_sync import synchronize_email
from app.providers.email_mock import MockEmailProvider


class FailingProvider(MockEmailProvider):
    def fetch_new_emails(self):
        raise RuntimeError("fake-token=secret-body")


def test_sync_error_is_fixed_and_audit_excludes_exception_text(tmp_path):
    database = tmp_path / "crm.db"
    initialize_database(database)
    events = []
    result = synchronize_email(database, FailingProvider(), events.append)

    assert result.error == "Email synchronization failed. Please try again."
    assert "fake-token" not in result.error
    assert "secret-body" not in result.error
    assert [event["outcome"] for event in events] == ["started", "failure"]
    assert all("fake-token" not in str(event) for event in events)


def test_sync_audits_success_with_only_safe_fields(tmp_path):
    database = tmp_path / "crm.db"
    initialize_database(database)
    result = synchronize_email(database, MockEmailProvider())

    assert result.imported >= 1
    with open_database(database) as connection:
        rows = connection.execute(
            "SELECT action, details FROM audit_log WHERE action = 'email_sync'"
        ).fetchall()
    assert len(rows) == 2
    assert '"outcome": "started"' in rows[0]["details"]
    assert '"outcome": "success"' in rows[1]["details"]
    assert all("body" not in row["details"] for row in rows)
