"""Focused tests for the M14 FAQ editor."""

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from app.db.database import initialize_database, open_database
from app.db.faqs import add_faq, list_faqs
from app.ui.faq_view import FAQView
from app.ui.main_window import MainWindow


@pytest.fixture
def qapp():
    application = QApplication.instance() or QApplication([])
    yield application


def test_faq_view_shows_status_and_test_box_does_not_save(qapp, tmp_path: Path):
    database_path = tmp_path / "faq-ui.db"
    initialize_database(database_path)
    add_faq(database_path, "What are your opening hours?",
            "We are open Monday to Friday.", approved=True)
    view = FAQView(database_path)

    assert view.table.rowCount() == 1
    assert view.table.item(0, 1).text() == "approved"
    before = len(list_faqs(database_path))
    view.sample_email_input.setPlainText("What are your opening hours?")
    view.test_sample()

    assert view.decision_label.text() == "Decision: Draft"
    assert "We are open Monday to Friday." in view.draft_label.text()
    assert len(list_faqs(database_path)) == before
    with open_database(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 1


def test_faq_view_approve_and_deactivate_use_database_api(qapp, tmp_path: Path, monkeypatch):
    database_path = tmp_path / "faq-actions.db"
    initialize_database(database_path)
    add_faq(database_path, "How long is delivery?", "Delivery takes two days.")
    view = FAQView(database_path)
    view.table.selectRow(0)
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    view.approve()
    assert list_faqs(database_path)[0].status == "approved"
    view.deactivate()
    assert list_faqs(database_path)[0].status == "inactive"


def test_faq_view_is_visible_and_escalation_still_shows_match_score(qapp, tmp_path: Path):
    database_path = tmp_path / "faq-escalation-ui.db"
    initialize_database(database_path)
    add_faq(database_path, "What is your refund policy?",
            "Refunds are handled by the support team.", approved=True)
    window = MainWindow(database_path)

    # Saved answers have a direct entry in the flat workspace navigation.
    assert window.pages.indexOf(window.faq_widget) == 3
    window.navigate(3)
    assert window.pages.currentWidget() is window.faq_widget
    view = window.faq_widget
    view.sample_email_input.setPlainText("I want a refund for my order.")
    view.test_sample()

    assert view.decision_label.text().startswith("Decision: Escalate")
    assert "refund" in view.matched_label.text().lower()
    assert view.score_label.text() != "Score: —"
    assert len(list_faqs(database_path)) == 1
