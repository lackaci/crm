"""Test-app workflow approval uses fictional records and fake Gmail only."""
from contextlib import closing
from dataclasses import replace
from pathlib import Path
import time
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from app.config import AppConfig, RunMode
from app.db.database import initialize_database, open_database
from app.db.customers import add_customer
from app.db.emails import import_emails
from app.db.connections import save_connection, set_mail_paused
from app.db.faqs import update_faq, approve_faq
from app.providers.email_base import DeliveryUncertain
from app.providers.email_mock import MockEmailProvider
from app.providers.llm_fake import FakeLLMProvider
from app.reply.delivery import list_outbox
from app.test_workflow import create_test_workspace, approve_workflow, run_workflow
from app.ui.test_workspace import TestWindow


@pytest.fixture
def workspace(tmp_path):
    path, storage = create_test_workspace(tmp_path / "test-app")
    return SimpleNamespace(path=path, storage=storage, config=AppConfig(RunMode.MOCK, "mock"))


class NoNetwork:
    def __getattr__(self, name):
        raise AssertionError("Offline mode must not access a network provider")


def test_normal_workspace_cannot_approve_test_workflow(tmp_path):
    path = tmp_path / "normal.sqlite"
    initialize_database(path)
    with pytest.raises(ValueError, match="separate test workspace"):
        approve_workflow(path, AppConfig(RunMode.MOCK, "mock"))


def test_test_workspace_is_persistent_and_does_not_reseed(workspace):
    again, storage = create_test_workspace(workspace.path.parent)
    assert again == workspace.path and storage == workspace.storage
    with closing(open_database(again)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM emails").fetchone()[0] == 5
        assert connection.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 3


def test_offline_approval_simulates_batch_without_any_provider_calls(workspace):
    approval = approve_workflow(workspace.path, workspace.config)
    result = run_workflow(approval, NoNetwork(), NoNetwork())
    assert result["simulated_sent"] == 2 and result["attention"] == 3
    assert list_outbox(workspace.path) == []
    again = run_workflow(approval, NoNetwork(), NoNetwork())
    assert again["simulated_sent"] == 0
    with closing(open_database(workspace.path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM draft_replies").fetchone()[0] == 2


def test_session_limit_and_stop_are_enforced(workspace):
    approval = approve_workflow(workspace.path, workspace.config, batch_size=5, session_limit=5)
    run_workflow(approval, NoNetwork())
    with pytest.raises(ValueError, match="limit"):
        run_workflow(approval, NoNetwork())
    approval = approve_workflow(workspace.path, workspace.config)
    approval.revoked.set()
    with pytest.raises(ValueError, match="stopped"):
        run_workflow(approval, NoNetwork())


def test_saved_settings_or_pause_invalidate_approval(workspace):
    approval = approve_workflow(workspace.path, workspace.config)
    save_connection(workspace.path, {"MINI_CRM_PROVIDER": "mock"})
    with pytest.raises(ValueError, match="Settings changed"):
        run_workflow(approval, NoNetwork())
    approval = approve_workflow(workspace.path, workspace.config)
    set_mail_paused(workspace.path, True)
    with pytest.raises(ValueError, match="paused"):
        run_workflow(approval, NoNetwork())


class Mail:
    provider_name = "gmail"
    expected_mailbox = "owner@example.invalid"
    def __init__(self):
        self.created = self.sent = 0
        self.on_verify = None
        self.uncertain = False
    def verify_connection(self, **options):
        if self.on_verify:
            action, self.on_verify = self.on_verify, None
            action()
    def create_reviewed_draft(self, reply):
        assert reply.sender == self.expected_mailbox
        self.created += 1
        return "remote-draft"
    def send_reviewed_draft(self, reply, draft_id):
        assert reply.recipient == "customer@example.invalid" and draft_id == "remote-draft"
        self.sent += 1
        if self.uncertain:
            raise DeliveryUncertain("Check Gmail status; no retry.")
        return "remote-message"


@pytest.fixture
def live(workspace):
    class Incoming:
        def fetch_new_emails(self):
            return [{"provider": "gmail", "source_mailbox": Mail.expected_mailbox, "remote_message_id": "incoming",
                     "from_name": "Customer", "from_email": "customer@example.invalid", "subject": "Opening hours?",
                     "body": "When are you open?"}]
    import_emails(workspace.path, Incoming())
    # Adding the sender after import must still make the email eligible.
    add_customer(workspace.path, "Customer", "customer@example.invalid")
    config = AppConfig(RunMode.LIVE, "gmail", test_mailbox=Mail.expected_mailbox, gmail_write_enabled=True,
                       enable_sending=True, send_allowlist=("customer@example.invalid",))
    return SimpleNamespace(**vars(workspace), provider=Mail(), live_config=config)


def test_session_approval_sends_once_without_per_message_dialogs(live):
    approval = approve_workflow(live.path, live.live_config, offline=False)
    result = run_workflow(approval, live.provider, FakeLLMProvider())
    assert result["sent"] == 1 and live.provider.created == live.provider.sent == 1
    assert list_outbox(live.path)[0]["status"] == "sent"
    assert run_workflow(approval, live.provider)["sent"] == 0
    assert live.provider.sent == 1


def test_live_send_requires_enabled_permissions_and_allowed_recipient(live):
    with pytest.raises(ValueError, match="Enable sending"):
        approve_workflow(live.path, replace(live.live_config, enable_sending=False), offline=False)
    approval = approve_workflow(live.path, replace(live.live_config, send_allowlist=("other@example.invalid",)), offline=False)
    result = run_workflow(approval, live.provider, NoNetwork())
    assert result["sent"] == 0 and result["attention"] == 1
    assert live.provider.created == live.provider.sent == 0


def test_stop_after_profile_verification_prevents_remote_write(live):
    approval = approve_workflow(live.path, live.live_config, offline=False)
    live.provider.on_verify = approval.revoked.set
    assert run_workflow(approval, live.provider)["sent"] == 0
    assert live.provider.created == live.provider.sent == 0


def test_stop_during_model_matching_blocks_later_model_calls(live):
    approval = approve_workflow(live.path, live.live_config, offline=False)
    class Model(FakeLLMProvider):
        calls = 0
        def embed(self, text):
            self.calls += 1
            approval.revoked.set()
            return super().embed(text)
    model = Model()
    run_workflow(approval, live.provider, model)
    assert model.calls == 1
    assert live.provider.created == live.provider.sent == 0


def test_faq_changed_during_verification_cannot_be_sent(live):
    approval = approve_workflow(live.path, live.live_config, offline=False)
    def change():
        update_faq(live.path, 1, "What are your opening hours?", "Changed answer.")
        approve_faq(live.path, 1)
    live.provider.on_verify = change
    assert run_workflow(approval, live.provider)["sent"] == 0
    assert live.provider.created == live.provider.sent == 0


def test_uncertain_send_stops_and_cannot_be_retried_by_new_approval(live):
    approval = approve_workflow(live.path, live.live_config, offline=False)
    live.provider.uncertain = True
    assert run_workflow(approval, live.provider)["attention"] == 1
    assert approval.revoked.is_set() and list_outbox(live.path)[0]["status"] == "unknown"
    again = approve_workflow(live.path, live.live_config, offline=False)
    run_workflow(again, live.provider)
    assert live.provider.sent == 1


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def wait_workflow(qapp, window):
    deadline = time.monotonic() + 8
    while window.workflow_task is not None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert window.workflow_task is None


def test_single_setup_page_runs_offline_workflow_and_revokes_on_edit(workspace, qapp):
    window = TestWindow(workspace.path, storage_root=workspace.storage, config=workspace.config)
    assert window.pages.currentWidget() is window.test_setup
    assert window.test_setup.isAncestorOf(window.settings_widget.gmail_widget)
    assert window.test_setup.isAncestorOf(window.files_widget.documents_widget.ai_settings_panel)
    assert not window.settings_widget.gmail_widget.isEnabled()
    assert not window.files_widget.documents_widget.processing_enabled
    window.test_setup.approve_button.click()
    wait_workflow(qapp, window)
    assert "2 simulated sent" in window.test_setup.results.toPlainText()
    assert "anna@example.com" in window.test_setup.output_preview.toPlainText()
    assert "9:00" in window.test_setup.output_preview.toPlainText()
    assert window.workflow_approval.remaining == 15
    window.test_setup.batch_input.setValue(2)
    assert window.workflow_approval.revoked.is_set()
    assert not window.test_setup.run_button.isEnabled()
    assert list_outbox(workspace.path) == []
    window.close()


def test_key_edit_revokes_without_showing_secret_and_add_email_works(workspace, qapp):
    window = TestWindow(workspace.path, storage_root=workspace.storage, config=workspace.config)
    window.test_setup.approve_button.click()
    wait_workflow(qapp, window)
    window.files_widget.documents_widget.api_key_input.setText("PRIVATE-TEST-KEY")
    assert "PRIVATE" not in window.test_setup.status.text()
    assert window.workflow_approval.revoked.is_set()
    window.test_setup.email_button.click()
    assert "Sample email added" in window.test_setup.status.text()
    window.close()


def test_restart_always_requires_a_new_approval(workspace, qapp):
    first = TestWindow(workspace.path, storage_root=workspace.storage, config=workspace.config)
    first.test_setup.approve_button.click()
    wait_workflow(qapp, first)
    first.close()
    second = TestWindow(workspace.path, storage_root=workspace.storage, config=workspace.config)
    assert second.workflow_approval is None and second.test_offline
    assert not second.test_setup.run_button.isEnabled()
    second.close()


def test_offline_mode_cannot_be_changed_while_connection_is_active(workspace, qapp):
    window = TestWindow(workspace.path, storage_root=workspace.storage, config=workspace.config)
    control = window.settings_widget.gmail_widget
    control.task = object()
    window.test_setup.offline_checkbox.setChecked(False)
    assert window.test_offline and window.test_setup.offline_checkbox.isChecked()
    assert "Wait" in window.test_setup.status.text()
    control.task = None
    window.close()
