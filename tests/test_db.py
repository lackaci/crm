"""Focused tests for FAQ and reply-draft persistence."""

import pytest

from app.db.database import initialize_database, open_database
from app.db.drafts import (
    create_draft_reply,
    get_draft,
    list_drafts,
    set_draft_status,
)
from app.db.emails import import_emails
from app.db.faqs import create_faq, list_approved_faqs
from app.providers.email_mock import MockEmailProvider


@pytest.fixture
def database_path(tmp_path):
    path = tmp_path / "reply.db"
    initialize_database(path)
    return path


def _add_email(database_path):
    class FixedProvider(MockEmailProvider):
        def fetch_new_emails(self):
            return [
                {
                    "id": "message-1",
                    "from_name": "Ada",
                    "from_email": "ada@example.com",
                    "subject": "Question",
                    "body": "Can you help?",
                }
            ]

    import_emails(database_path, FixedProvider())
    with open_database(database_path) as connection:
        return int(connection.execute("SELECT id FROM emails").fetchone()["id"])


def test_only_approved_faqs_are_listed(database_path):
    create_faq(database_path, "One", "Answer one")
    approved_id = create_faq(database_path, "Two", "Answer two", approved=True)

    faqs = list_approved_faqs(database_path)

    assert [faq.id for faq in faqs] == [approved_id]
    assert faqs[0].approved is True


def test_draft_links_approved_faq_and_records_audit(database_path):
    email_id = _add_email(database_path)
    faq_id = create_faq(database_path, "Question", "Answer", approved=True)

    draft_id = create_draft_reply(database_path, email_id, "A reply.", faq_id)

    assert get_draft(database_path, draft_id).faq_id == faq_id
    with open_database(database_path) as connection:
        actions = [
            row["action"]
            for row in connection.execute(
                "SELECT action FROM audit_log ORDER BY id"
            ).fetchall()
        ]
    assert "draft_created" in actions


def test_unapproved_faq_cannot_be_linked_to_draft(database_path):
    email_id = _add_email(database_path)
    faq_id = create_faq(database_path, "Question", "Answer")

    with pytest.raises(ValueError, match="approved"):
        create_draft_reply(database_path, email_id, "A reply.", faq_id)


def test_status_update_validates_status_and_audits(database_path):
    email_id = _add_email(database_path)
    draft_id = create_draft_reply(database_path, email_id, "A reply.")

    set_draft_status(database_path, draft_id, "approved", actor="reviewer")

    assert list_drafts(database_path)[0].status == "approved"
    with pytest.raises(ValueError, match="Invalid draft status"):
        set_draft_status(database_path, draft_id, "sent")
    with open_database(database_path) as connection:
        audit = connection.execute(
            "SELECT actor, action, details FROM audit_log "
            "WHERE action = ?",
            ("draft_status_changed",),
        ).fetchone()
    assert audit["actor"] == "reviewer"
    assert "status=approved" in audit["details"]
