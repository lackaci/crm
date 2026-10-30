"""Focused tests for the approval queue service."""

import pytest

from app.db.database import initialize_database, open_database
from app.db.drafts import create_draft_reply
from app.db.emails import import_emails
from app.providers.email_base import EmailDeliveryError, EmailProvider
from app.reply.approval import approve_draft, edit_draft, list_pending_drafts, reject_draft


class RecordingProvider(EmailProvider):
    """Provider test double which records calls and can fail."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.fail = fail

    def fetch_new_emails(self) -> list[dict[str, str]]:
        return []

    def send_email(self, to: str, subject: str, body: str) -> None:
        self.calls.append((to, subject, body))
        if self.fail:
            raise EmailDeliveryError("provider unavailable")


@pytest.fixture
def database_path(tmp_path):
    path = tmp_path / "approval.db"
    initialize_database(path)
    with open_database(path) as connection:
        connection.execute(
            """
            INSERT INTO emails (from_name, from_email, subject, body)
            VALUES (?, ?, ?, ?)
            """,
            ("Ada", "ada@example.com", "Question", "Can you help?"),
        )
        email_id = int(connection.execute("SELECT id FROM emails").fetchone()["id"])
    return path, email_id


def _draft(database_path, email_id):
    return create_draft_reply(database_path, email_id, "A reply.")


def _actions(database_path):
    with open_database(database_path) as connection:
        return [
            row["action"]
            for row in connection.execute(
                "SELECT action FROM audit_log ORDER BY id"
            ).fetchall()
        ]


def test_approve_is_hard_blocked_even_when_sending_enabled(database_path):
    path, email_id = database_path
    draft_id = _draft(path, email_id)
    provider = RecordingProvider()

    with pytest.raises(ValueError, match="until the sending milestone"):
        approve_draft(path, draft_id, provider, actor="reviewer",
                      sending_enabled=True)

    assert provider.calls == []
    assert list_pending_drafts(path)[0].status == "pending"
    assert "draft_send_blocked" in _actions(path)


def test_approve_is_blocked_by_default_before_provider_call(database_path):
    path, email_id = database_path
    draft_id = _draft(path, email_id)
    provider = RecordingProvider()

    with pytest.raises(ValueError, match="Sending is disabled"):
        approve_draft(path, draft_id, provider)

    assert provider.calls == []
    assert list_pending_drafts(path)[0].status == "pending"
    assert "draft_send_blocked" in _actions(path)


def test_edit_and_reject_never_send_and_audit(database_path):
    path, email_id = database_path
    edit_id = _draft(path, email_id)
    reject_id = _draft(path, email_id)
    provider = RecordingProvider()

    assert edit_draft(path, edit_id, "Edited.", actor="reviewer").body == "Edited."
    assert reject_draft(path, reject_id, actor="reviewer").status == "rejected"

    assert provider.calls == []
    actions = _actions(path)
    assert "draft_edited" in actions
    assert "draft_rejected" in actions


def test_approval_never_reaches_provider_or_audits_provider_failure(database_path):
    path, email_id = database_path
    draft_id = _draft(path, email_id)
    provider = RecordingProvider(fail=True)

    with pytest.raises(ValueError, match="until the sending milestone"):
        approve_draft(path, draft_id, provider, sending_enabled=True)

    with open_database(path) as connection:
        status = connection.execute(
            "SELECT status FROM draft_replies WHERE id = ?", (draft_id,)
        ).fetchone()["status"]
    assert status == "pending"
    assert "draft_send_blocked" in _actions(path)
    assert "draft_send_failed" not in _actions(path)


def test_only_pending_drafts_can_be_acted_on(database_path):
    path, email_id = database_path
    draft_id = _draft(path, email_id)
    reject_draft(path, draft_id)

    with pytest.raises(ValueError, match="pending"):
        approve_draft(
            path, draft_id, RecordingProvider(), sending_enabled=True
        )
    assert list_pending_drafts(path) == []
