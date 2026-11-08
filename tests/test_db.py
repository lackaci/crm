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
from app.db.faqs import (
    add_faq,
    approve_faq,
    create_faq,
    deactivate_faq,
    list_approved_faqs,
    list_faqs,
    update_faq,
)
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


def test_faq_lifecycle_audits_and_edit_requires_reapproval(database_path):
    faq_id = add_faq(database_path, "Question", "Answer", actor="owner")

    assert list_faqs(database_path, status="draft")[0].status == "draft"
    update_faq(database_path, faq_id, "Edited question", "Edited answer", actor="owner")
    approve_faq(database_path, faq_id, actor="owner")
    deactivate_faq(database_path, faq_id, actor="owner")

    assert list_approved_faqs(database_path) == []
    assert list_faqs(database_path, status="inactive")[0].approved is False
    with open_database(database_path) as connection:
        actions = [
            row["action"]
            for row in connection.execute(
                "SELECT action FROM audit_log ORDER BY id"
            ).fetchall()
        ]
    assert actions[-4:] == [
        "faq_created",
        "faq_updated",
        "faq_approved",
        "faq_deactivated",
    ]


def test_approved_faq_edit_clears_approval_until_reapproved(database_path):
    faq_id = add_faq(
        database_path, "Original question", "Original answer", approved=True,
        actor="reviewer",
    )
    initial = list_faqs(database_path)[0]
    assert initial.status == "approved"
    assert initial.approved_at is not None

    update_faq(
        database_path, faq_id, "Edited question", "Edited answer",
        actor="reviewer",
    )
    edited = list_faqs(database_path)[0]
    assert edited.status == "draft"
    assert edited.approved is False
    assert edited.approved_at is None

    approve_faq(database_path, faq_id, actor="reviewer")
    approved_again = list_faqs(database_path)[0]
    assert approved_again.status == "approved"
    assert approved_again.approved is True
    assert approved_again.approved_at is not None

    with open_database(database_path) as connection:
        actions = [
            row["action"]
            for row in connection.execute(
                "SELECT action FROM audit_log WHERE actor = ? ORDER BY id",
                ("reviewer",),
            ).fetchall()
        ]
    assert actions == ["faq_created", "faq_updated", "faq_approved"]


def test_legacy_faqs_are_migrated_to_approved(database_path, tmp_path):
    # Recreate the legacy shape in a separate file to exercise startup migration.
    legacy = tmp_path / "legacy-faq.db"
    with open_database(legacy) as connection:
        connection.execute(
            "CREATE TABLE faq_entries ("
            "id INTEGER PRIMARY KEY, question TEXT NOT NULL, answer TEXT NOT NULL, "
            "approved INTEGER NOT NULL DEFAULT 0, "
            "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO faq_entries (question, answer, approved) VALUES (?, ?, ?)",
            ("Old question", "Old answer", 0),
        )
    initialize_database(legacy)

    faq = list_faqs(legacy)[0]
    assert faq.status == "approved"
    assert faq.approved_at is not None
    assert list_approved_faqs(legacy)[0].id == faq.id
