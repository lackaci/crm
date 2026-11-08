"""User journeys through the simplified desktop workspace, using fictional data."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from app.config import AppConfig, RunMode
from app.demo import create_demo_workspace
from app.db.customers import list_customers
from app.db.drafts import list_drafts
from app.providers.email_mock import MockEmailProvider
from app.providers.llm_fake import FakeLLMProvider
from app.ui.main_window import MainWindow


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def workspace(qapp, tmp_path):
    database, storage = create_demo_workspace(tmp_path)
    window = MainWindow(database, email_provider=MockEmailProvider(), llm_provider=FakeLLMProvider(),
        storage_root=storage, config=AppConfig(RunMode.MOCK, "mock"), demo_mode=True)
    yield window
    window.close()


def test_demo_opens_ready_to_read_and_prepare_without_setup(workspace):
    window = workspace
    assert window.pages.currentWidget() is window.inbox_widget
    assert "Anna" in window.customer_selector.currentText()
    assert "Fridays" in window.inbox_widget.email_preview.toPlainText()
    assert window.inbox_widget.create_draft_button.isEnabled()
    assert window.faq_widget.table.rowCount() == 4
    assert window.inbox_widget.unmatched_table.rowCount() == 2
    assert not window.approval_widget.approve_button.isEnabled()


def test_preparing_reply_opens_the_created_draft_for_review(workspace):
    window = workspace
    window.inbox_widget.create_draft_button.click()
    assert window.pages.currentWidget() is window.approval_widget
    assert "anna@example.com" in window.approval_widget.recipient_label.text()
    assert "9:00" in window.approval_widget.body_editor.toPlainText()
    assert "(1)" in window.navigation.item(2).text()
    assert window.approval_widget.copy_button.isEnabled()
    assert not window.approval_widget.approve_button.isEnabled()


def test_actual_mouse_click_on_message_and_prepare_opens_review(workspace, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    window = workspace
    window.show()
    qapp.processEvents()
    inbox = window.inbox_widget
    email_id = inbox.selected_email_id
    row = next(index for index in range(inbox.email_table.rowCount())
               if inbox.email_table.item(index, 0).data(Qt.ItemDataRole.UserRole) == email_id)
    entry = inbox.email_table.item(row, 6)
    inbox.email_table.scrollToItem(entry)
    qapp.processEvents()
    QTest.mouseClick(inbox.email_table.viewport(), Qt.MouseButton.LeftButton,
                     pos=inbox.email_table.visualItemRect(entry).center())
    QTest.mouseClick(inbox.create_draft_button, Qt.MouseButton.LeftButton)
    assert window.pages.currentWidget() is window.approval_widget
    assert len(list_drafts(window.database_path)) == 1


def test_prepare_without_saved_answers_opens_editable_clarification(workspace):
    from contextlib import closing
    from app.db.database import open_database
    window = workspace
    inbox = window.inbox_widget
    with closing(open_database(window.database_path)) as connection, connection:
        connection.execute("UPDATE faq_entries SET status='draft',approved=0")
    inbox.create_draft_button.click()
    assert window.pages.currentWidget() is window.approval_widget
    assert 'No relevant approved answer' in window.approval_widget.source_faq_label.toPlainText()
    assert 'needs to check' in window.approval_widget.body_editor.toPlainText()
    assert len(list_drafts(window.database_path)) == 1
    assert not window.approval_widget.body_editor.isReadOnly()


def test_demo_import_button_stays_offline(workspace):
    from PySide6.QtTest import QSignalSpy
    inbox = workspace.inbox_widget
    setup = QSignalSpy(inbox.gmail_setup_requested)
    assert inbox.import_button.text() == "Import example emails"
    inbox.import_button.click()
    assert setup.count() == 0
    assert inbox.email_table.rowCount() == 5


def test_customer_and_message_selection_survive_navigation_and_status_refresh(workspace):
    window = workspace
    customer_id = window.customers_widget.selected_customer_id
    email_id = window.inbox_widget.selected_email_id
    window.customers_widget.refresh()
    window.navigate(4)
    assert window.files_widget.selected_customer_id == customer_id
    window.navigate(1)
    window.navigate(0)
    assert window.customer_selector.currentData() == customer_id
    assert window.inbox_widget.selected_email_id == email_id
    assert window.inbox_widget.create_draft_button.isEnabled()


def test_unassigned_view_cannot_prepare_hidden_customer_message(workspace):
    window = workspace
    inbox = window.inbox_widget
    inbox.inbox_tabs.setCurrentIndex(1)
    inbox.unmatched_table.selectRow(0)
    assert inbox.selected_email_id is None
    selected = inbox.selected_unmatched_id
    assert inbox.create_draft_button.text() == "Prepare reply"
    inbox.create_draft()
    drafts = list_drafts(window.database_path)
    assert len(drafts) == 1 and drafts[0].email_id == selected
    assert not inbox.assignment_panel.isHidden()


def test_linking_email_opens_it_under_chosen_customer(workspace):
    window = workspace
    peter = next(customer for customer in list_customers(window.database_path) if customer.name.startswith("Peter"))
    inbox = window.inbox_widget
    inbox.inbox_tabs.setCurrentIndex(1)
    inbox.unmatched_table.selectRow(0)
    inbox.customer_combo.setCurrentIndex(inbox.customer_combo.findData(peter.id))
    inbox.assign_button.click()
    assert window.customer_selector.currentData() == peter.id
    assert inbox.inbox_tabs.currentIndex() == 0
    assert inbox.selected_email_id is not None
    assert inbox.unmatched_table.rowCount() == 1


def test_customer_save_keeps_the_current_customer_and_updates_shared_picker(workspace):
    window = workspace
    widget = window.customers_widget
    customer_id = widget.selected_customer_id
    widget.name_input.setText("Anna Demo")
    widget.save_customer()
    assert widget.selected_customer_id == customer_id
    assert "Anna Demo" in window.customer_selector.currentText()
    assert window.inbox_widget.customer_id == customer_id
    widget.clear_button.click()
    assert widget.selected_customer_id is None and window.customer_selector.currentData() is None
    widget.name_input.setText("New Demo")
    widget.email_input.setText("new@example.com")
    widget.save_button.click()
    assert "New Demo" in window.customer_selector.currentText()


def test_message_search_disables_actions_when_selection_is_filtered_out(workspace, qapp):
    from PySide6.QtTest import QTest
    inbox = workspace.inbox_widget
    inbox.search_input.setText("does not exist")
    QTest.qWait(250)
    assert inbox.email_table.rowCount() == 0
    assert inbox.selected_email_id is None
    assert not inbox.create_draft_button.isEnabled()


def test_copy_and_save_reviewed_reply_stay_local(workspace, monkeypatch):
    window = workspace
    window.inbox_widget.create_draft_button.click()
    copied = []
    monkeypatch.setattr(QApplication, "clipboard", staticmethod(lambda: SimpleNamespace(setText=copied.append)))
    window.approval_widget.body_editor.setPlainText("Edited reply for demo")
    window.approval_widget.copy_button.click()
    assert copied == ["Edited reply for demo"]
    window.approval_widget.edit_button.click()
    draft = list_drafts(window.database_path)[0]
    assert draft.body == "Edited reply for demo" and draft.status == "pending"
    window.approval_widget.approve()
    assert list_drafts(window.database_path)[0].status == "pending"


def test_demo_launch_ignores_live_provider_configuration_and_cleans_up(qapp, monkeypatch):
    import app.main as entry
    captured = {}
    class DemoWindow:
        def __init__(self, database_path, **kwargs):
            captured["database"] = database_path
            assert kwargs["demo_mode"] is True
            assert kwargs["config"].mode is RunMode.MOCK
            assert isinstance(kwargs["email_provider"], MockEmailProvider)
            assert database_path.name == "demo.sqlite"
        def show(self):
            pass
        def close(self):
            pass
    monkeypatch.setenv("MINI_CRM_PROVIDER", "gmail")
    monkeypatch.setenv("MINI_CRM_MODE", "Live")
    monkeypatch.setattr(entry, "load_config", lambda: pytest.fail("Demo must not load the real configuration"))
    monkeypatch.setattr(entry, "GmailProvider", lambda *args, **kwargs: pytest.fail("Demo must never create Gmail"))
    monkeypatch.setattr(entry, "MainWindow", DemoWindow)
    monkeypatch.setattr(QApplication, "exec", lambda self: 0)
    monkeypatch.setattr("sys.argv", ["mini-crm", "--demo"])
    assert entry.main() == 0
    assert not captured["database"].exists()


def test_demo_seed_refuses_to_overwrite_existing_workspace(tmp_path):
    database, _ = create_demo_workspace(tmp_path)
    with pytest.raises(FileExistsError):
        create_demo_workspace(tmp_path)
    assert len(list_customers(database)) == 3


def test_risky_message_opens_clarification_with_personal_review_warning(workspace):
    window = workspace
    maria = next(customer for customer in list_customers(window.database_path) if customer.name.startswith("Maria"))
    window.customer_selector.setCurrentIndex(window.customer_selector.findData(maria.id))
    window.inbox_widget.email_table.selectRow(0)
    window.inbox_widget.create_draft_button.click()
    assert window.pages.currentWidget() is window.approval_widget
    assert "Sensitive request" in window.approval_widget.source_faq_label.toPlainText()
    assert 'needs to check' in window.approval_widget.body_editor.toPlainText()
    assert len(list_drafts(window.database_path)) == 1


def test_reading_completed_email_preserves_its_status(workspace):
    from app.db.emails import set_email_status
    inbox = workspace.inbox_widget
    email_id = inbox.selected_email_id
    set_email_status(workspace.database_path, email_id, "done")
    inbox.set_customer(workspace.customers_widget.selected_customer_id)
    inbox.open_message(email_id, workspace.customers_widget.selected_customer_id)
    assert "Done" in inbox.message_details.text()


def test_demo_imported_attachments_stay_in_demo_storage(workspace, tmp_path, monkeypatch):
    from app.db.files import list_files
    import app.db.emails as email_database
    outside = tmp_path / "ordinary-storage-must-stay-empty"
    monkeypatch.setattr(email_database, "DEFAULT_STORAGE_ROOT", outside)
    class AttachmentProvider(MockEmailProvider):
        def fetch_new_emails(self):
            return [{"id": "new-demo-attachment", "from_name": "Anna", "from_email": "anna@example.com",
                "subject": "Example attachment", "body": "Fictional attachment",
                "attachments": [{"attachment_id": "attachment-1", "filename": "example.txt", "content": b"fictional"}]}]
    workspace.inbox_widget.provider = AttachmentProvider()
    workspace.inbox_widget.import_sample_emails()
    customer_id = workspace.customers_widget.selected_customer_id
    files = [record for record in list_files(workspace.database_path, customer_id) if record.source_email_id is not None]
    assert len(files) == 1
    stored = workspace.files_widget.storage_root / files[0].path
    assert stored.read_bytes() == b"fictional"
    assert not outside.exists()
