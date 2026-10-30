"""Focused tests for the approval queue widget."""

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from app.db.database import initialize_database, open_database
from app.db.drafts import create_draft_reply
from app.providers.email_base import EmailDeliveryError, EmailProvider
from app.reply.approval import list_pending_drafts
from app.ui.approval import ApprovalQueueWidget


class RecordingProvider(EmailProvider):
    def __init__(self, fail: bool = False) -> None:
        self.calls = []
        self.fail = fail

    def fetch_new_emails(self):
        return []

    def send_email(self, to, subject, body):
        self.calls.append((to, subject, body))
        if self.fail:
            raise EmailDeliveryError("send failed")


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def draft_database(tmp_path: Path):
    path = tmp_path / "approval-ui.db"
    initialize_database(path)
    with open_database(path) as connection:
        connection.execute(
            "INSERT INTO emails (from_name, from_email, subject, body) "
            "VALUES (?, ?, ?, ?)",
            ("Ada", "ada@example.com", "Question", "Incoming message"),
        )
        email_id = int(connection.execute("SELECT id FROM emails").fetchone()["id"])
    draft_id = create_draft_reply(path, email_id, "Proposed answer")
    return path, draft_id


def test_pending_draft_is_readable_and_edit_does_not_send(
    qapp, draft_database
):
    path, draft_id = draft_database
    provider = RecordingProvider()
    widget = ApprovalQueueWidget(path, provider=provider)

    assert widget.draft_list.count() == 1
    assert "ada@example.com" in widget.draft_list.item(0).text()
    assert widget.body_editor.toPlainText() == "Proposed answer"

    widget.body_editor.setPlainText("Edited answer")
    widget.save_edit()

    assert list_pending_drafts(path)[0].body == "Edited answer"
    assert provider.calls == []
    assert widget.draft_list.count() == 1


def test_empty_queue_shows_status_and_disables_actions(qapp, tmp_path: Path):
    path = tmp_path / "empty-approval-ui.db"
    initialize_database(path)

    widget = ApprovalQueueWidget(path)

    assert widget.empty_state_label.text() == "No pending drafts."
    assert not widget.empty_state_label.isHidden()
    assert not widget.edit_button.isEnabled()
    assert not widget.reject_button.isEnabled()
    assert not widget.approve_button.isEnabled()


def test_default_widget_disables_approve_and_explains_why(qapp, draft_database):
    path, _draft_id = draft_database

    widget = ApprovalQueueWidget(path)

    assert not widget.approve_button.isEnabled()
    assert "sending is disabled" in widget.approve_button.toolTip().lower()


def test_selecting_pending_draft_enables_actions(qapp, draft_database):
    path, _draft_id = draft_database

    widget = ApprovalQueueWidget(path, sending_enabled=True)

    assert widget.edit_button.isEnabled()
    assert widget.reject_button.isEnabled()
    assert not widget.approve_button.isEnabled()
    assert widget.empty_state_label.isHidden()


def test_reject_does_not_send_and_approve_stays_blocked(
    qapp, draft_database
):
    path, draft_id = draft_database
    provider = RecordingProvider()
    widget = ApprovalQueueWidget(
        path, provider=provider, sending_enabled=True
    )

    widget.reject()
    assert list_pending_drafts(path) == []
    assert provider.calls == []

    second_id = None
    with open_database(path) as connection:
        email_id = int(connection.execute("SELECT id FROM emails").fetchone()["id"])
    second_id = create_draft_reply(path, email_id, "Another answer")
    widget.refresh()
    widget.approve()

    assert provider.calls == []
    assert list_pending_drafts(path)[0].status == "pending"


def test_approval_button_has_no_enabled_provider_path(
    qapp, draft_database, monkeypatch
):
    path, _draft_id = draft_database
    provider = RecordingProvider(fail=True)
    widget = ApprovalQueueWidget(
        path, provider=provider, sending_enabled=True
    )

    assert not widget.approve_button.isEnabled()
    widget.approve()

    assert provider.calls == []
    assert len(list_pending_drafts(path)) == 1
