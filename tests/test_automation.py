"""Inbox automation uses fictional SQLite data and offline providers only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from types import SimpleNamespace
import time

import pytest
from PySide6.QtWidgets import QApplication

from app.automation import classify, run_automation
from app.config import AppConfig, RunMode
from app.db import automation as store
from app.db.connections import set_mail_paused
from app.db.customers import add_customer
from app.db.database import initialize_database, open_database
from app.db.drafts import create_draft_reply, list_drafts
from app.db.emails import import_emails, assign_email_to_customer, set_email_status, remove_gmail_emails_not_from
from app.db.faqs import create_faq, deactivate_faq
from app.email_sync import synchronize_email
from app.providers.email_mock import MockEmailProvider
from app.providers.factory import BackgroundGmailImport
from app.providers.gmail import GmailProvider
from app.providers.llm_fake import FakeLLMProvider
from app.ui.main_window import MainWindow


class Mailbox(MockEmailProvider):
    def __init__(self, subject="Opening hours?", body="When are you open on Fridays?", sender="customer@example.invalid"):
        self.subject, self.body, self.sender = subject, body, sender
        self.calls = 0
    def fetch_new_emails(self):
        self.calls += 1
        return [{"id": "message-1", "from_name": "Customer", "from_email": self.sender,
            "subject": self.subject, "body": self.body}]


@pytest.fixture
def workspace(tmp_path):
    database = tmp_path / "automation.sqlite"
    initialize_database(database)
    customer = add_customer(database, "Customer", "customer@example.invalid")
    faq = create_faq(database, "What are your opening hours?", "We are open Monday to Friday, 9:00 to 17:00.", approved=True)
    return SimpleNamespace(path=database, customer=customer, faq=faq)


def enable(workspace, **options):
    store.save_settings(workspace.path, enabled=True, **options)


def insert(workspace, **options):
    provider = Mailbox(**options)
    import_emails(workspace.path, provider)
    return provider


def test_automation_is_opt_in_and_pausing_calls_no_model(workspace):
    insert(workspace)
    class NoModel(FakeLLMProvider):
        def embed(self, *args):
            pytest.fail("Paused automation must not call a provider")
    assert run_automation(workspace.path, NoModel()).paused
    enable(workspace)
    set_mail_paused(workspace.path, True)
    assert run_automation(workspace.path, NoModel()).paused
    assert not store.history(workspace.path) and not list_drafts(workspace.path)


@pytest.mark.parametrize("subject,category", [
    ("Opening hours?", "question"), ("Please send a quote", "quote"),
    ("My invoice", "billing"), ("I am furious; refund please", "complaint"),
    ("A legal threat", "complaint"), ("A friendly update", "other")])
def test_categories_have_predictable_rules(subject, category):
    actual, reason = classify(SimpleNamespace(subject=subject, body="Hello"))
    assert actual == category and reason


def test_safe_reply_is_assigned_and_queued_once(workspace):
    insert(workspace)
    staff = store.add_staff(workspace.path, "Sam")
    rules = store.list_rules(workspace.path)
    rules["question"].update(staff_id=staff, priority="high")
    store.save_rules(workspace.path, rules)
    enable(workspace)
    result = run_automation(workspace.path)
    assert result.processed == result.drafts == 1
    triage = store.inbox_triage(workspace.path)[1]
    assert triage["staff_name"] == "Sam" and triage["priority"] == "high"
    draft = list_drafts(workspace.path)[0]
    assert draft.status == "pending" and "9:00" in draft.body
    assert "Approved answer" in store.history(workspace.path)[0]["reason"]
    assert run_automation(workspace.path).processed == 0
    assert len(store.history(workspace.path)) == len(list_drafts(workspace.path)) == 1


@pytest.mark.parametrize("body", ["Please refund this payment", "I will sue you", "This is unacceptable", "It costs 20 dollars"])
def test_risky_mail_never_reaches_a_model_or_creates_a_reply(workspace, body):
    insert(workspace, body=body)
    enable(workspace)
    class NoModel(FakeLLMProvider):
        def embed(self, *args):
            pytest.fail("Risky messages must not reach the matcher")
    result = run_automation(workspace.path, NoModel())
    event = store.history(workspace.path)[0]
    assert result.attention == 1 and result.drafts == 0
    assert event["outcome"] == "needs_attention" and event["priority"] == "high"
    assert not list_drafts(workspace.path)


def test_unknown_sender_waits_until_linked_then_processes_once(workspace):
    insert(workspace, sender="unknown@example.invalid")
    enable(workspace)
    assert run_automation(workspace.path).attention == 1
    assert not list_drafts(workspace.path)
    assert run_automation(workspace.path).processed == 0
    with closing(open_database(workspace.path)) as connection:
        customer = connection.execute("SELECT id FROM customers").fetchone()["id"]
    assign_email_to_customer(workspace.path, 1, customer)
    assert run_automation(workspace.path).drafts == 1
    assert len(store.history(workspace.path)) == 2
    assert run_automation(workspace.path).processed == 0


def test_new_customer_is_matched_without_reimporting(workspace):
    insert(workspace, sender="later@example.invalid")
    enable(workspace)
    run_automation(workspace.path)
    add_customer(workspace.path, "Later", "LATER@example.invalid")
    assert run_automation(workspace.path).drafts == 1
    with closing(open_database(workspace.path)) as connection:
        assert connection.execute("SELECT customer_id FROM emails").fetchone()[0] is not None


def test_ambiguous_customer_emails_stay_unassigned(workspace):
    add_customer(workspace.path, "Duplicate case", "CUSTOMER@example.invalid")
    insert(workspace)
    enable(workspace)
    assert run_automation(workspace.path).attention == 1
    assert store.history(workspace.path)[0]["outcome"] == "needs_assignment"
    assert run_automation(workspace.path).processed == 0


def test_existing_manual_reply_is_preserved(workspace):
    insert(workspace)
    create_draft_reply(workspace.path, 1, "Staff edited this manually")
    enable(workspace)
    result = run_automation(workspace.path)
    assert result.drafts == 0 and len(list_drafts(workspace.path)) == 1
    assert list_drafts(workspace.path)[0].body == "Staff edited this manually"
    assert store.history(workspace.path)[0]["outcome"] == "existing_reply"


@pytest.mark.parametrize("change", ["pause", "faq", "done", "rules"])
def test_stale_generation_cannot_commit_an_unsafe_or_paused_reply(workspace, change):
    insert(workspace)
    enable(workspace)
    class ChangedDuringGeneration(FakeLLMProvider):
        def complete(self, *args, **kwargs):
            text = super().complete(*args, **kwargs)
            if change == "pause":
                store.save_settings(workspace.path, enabled=False)
            elif change == "faq":
                deactivate_faq(workspace.path, 1)
            elif change == "done":
                set_email_status(workspace.path, 1, "done")
            else:
                store.save_rules(workspace.path, store.list_rules(workspace.path))
            return text
    result = run_automation(workspace.path, ChangedDuringGeneration())
    assert result.drafts == 0 and not list_drafts(workspace.path)
    if change == "faq":
        assert store.history(workspace.path)[0]["outcome"] == "needs_attention"
    else:
        assert not store.history(workspace.path)


def test_model_failure_is_visible_without_leaking_exception(workspace):
    insert(workspace)
    enable(workspace)
    class FailedModel(FakeLLMProvider):
        def embed(self, *args):
            raise RuntimeError("PRIVATE TOKEN AND CUSTOMER TEXT")
    assert run_automation(workspace.path, FailedModel()).attention == 1
    event = store.history(workspace.path)[0]
    assert "failed" in event["reason"] and "PRIVATE" not in event["reason"]


def test_two_workers_commit_only_one_reply_and_event(workspace):
    insert(workspace)
    enable(workspace)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: run_automation(workspace.path), range(2)))
    assert sum(result.drafts for result in results) == 1
    assert len(list_drafts(workspace.path)) == len(store.history(workspace.path)) == 1


def test_failed_history_write_rolls_back_reply_and_retries_safely(workspace):
    insert(workspace)
    enable(workspace)
    with closing(open_database(workspace.path)) as connection, connection:
        connection.execute("CREATE TRIGGER abort_history BEFORE INSERT ON desktop_automation_events BEGIN SELECT RAISE(ABORT,'fake failure'); END")
    with pytest.raises(Exception):
        run_automation(workspace.path)
    assert not list_drafts(workspace.path) and not store.inbox_triage(workspace.path)
    with closing(open_database(workspace.path)) as connection, connection:
        connection.execute("DROP TRIGGER abort_history")
    assert run_automation(workspace.path).drafts == 1


def test_import_runs_triage_but_repeat_import_does_not_duplicate(workspace):
    enable(workspace)
    provider = Mailbox()
    result = synchronize_email(workspace.path, provider)
    assert result.imported == result.automation.drafts == 1 and result.error is None
    repeated = synchronize_email(workspace.path, provider)
    assert repeated.imported == repeated.automation.processed == 0
    assert len(list_drafts(workspace.path)) == 1


def test_triage_failure_does_not_report_committed_import_as_failed(workspace, monkeypatch):
    def failure(*args, **kwargs):
        raise RuntimeError("PRIVATE")
    monkeypatch.setattr("app.automation.run_automation", failure)
    result = synchronize_email(workspace.path, Mailbox())
    assert result.imported == 1 and result.error is None and result.automation_error


def test_no_suggestion_without_approved_answer_or_when_disabled(workspace):
    insert(workspace)
    enable(workspace, auto_draft=False)
    assert run_automation(workspace.path).drafts == 0
    assert "disabled" in store.history(workspace.path)[0]["reason"]


def test_unapproved_faq_never_supplies_a_suggestion(workspace):
    insert(workspace)
    deactivate_faq(workspace.path, 1)
    enable(workspace)
    assert run_automation(workspace.path).drafts == 0
    assert store.history(workspace.path)[0]["outcome"] == "needs_attention"


def test_background_import_never_opens_google_login(monkeypatch):
    provider = GmailProvider(expected_mailbox="owner@example.invalid")
    calls = []
    def login(*, allow_login=True):
        calls.append(allow_login)
        raise RuntimeError("No cached token")
    monkeypatch.setattr(provider, "_service_or_login", login)
    with pytest.raises(Exception):
        BackgroundGmailImport(provider).fetch_new_emails()
    assert calls == [False]


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def wait_task(qapp, widget):
    end = time.monotonic() + 5
    while widget.task is not None and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    assert widget.task is None


def test_staff_rules_and_automation_have_a_direct_review_journey(workspace, qapp):
    insert(workspace)
    window = MainWindow(workspace.path, config=AppConfig(RunMode.MOCK, "mock"), demo_mode=True)
    control = window.automation_widget
    window.navigate(6)
    control.staff_input.setText("Sam")
    control.add_staff()
    assignee, _ = control.rule_controls["question"]
    assignee.setCurrentIndex(1)
    control.enabled_checkbox.setChecked(True)
    control.save()
    assert not window.automation_timer.isActive()
    control.run_now()
    wait_task(qapp, control)
    assert control.history_table.rowCount() == 1
    control.history_table.selectRow(0)
    assert "Approved answer" in control.reason_text.toPlainText()
    control.review_selected()
    assert window.pages.currentWidget() is window.approval_widget
    assert "9:00" in window.approval_widget.body_editor.toPlainText()
    control.open_selected()
    assert window.pages.currentWidget() is window.inbox_widget
    assert "Sam" in window.inbox_widget.email_table.item(0, 4).text()
    window.inbox_widget.create_draft()
    assert len(list_drafts(workspace.path)) == 1
    control.pause()
    assert not store.settings(workspace.path)["enabled"]
    window.close()


def test_scheduling_requires_opt_in_and_respects_pause_and_busy(workspace, qapp, monkeypatch):
    provider = GmailProvider(service=object(), expected_mailbox="owner@example.invalid")
    window = MainWindow(workspace.path, email_provider=provider,
        config=AppConfig(RunMode.DRY_RUN, "gmail", test_mailbox="owner@example.invalid"))
    calls = []
    monkeypatch.setattr(window.inbox_widget, "start_import", lambda automatic=False: calls.append(automatic))
    assert not window.automation_timer.isActive()
    window._scheduled_import()
    assert not calls
    enable(workspace, poll_seconds=45)
    window._configure_automation_timer()
    assert window.automation_timer.interval() == 45000
    window._scheduled_import()
    assert calls == [True]
    set_mail_paused(workspace.path, True)
    window._scheduled_import()
    assert calls == [True]
    set_mail_paused(workspace.path, False)
    window.automation_widget.task = object()
    window._scheduled_import()
    assert calls == [True]
    window.automation_widget.task = None
    window.close()


def test_schedule_triages_only_its_verified_source_mailbox(workspace):
    class TwoAccounts(Mailbox):
        def fetch_new_emails(self):
            record = super().fetch_new_emails()[0]
            return [dict(record, provider="gmail", source_mailbox=account, remote_message_id="same-id")
                for account in ("owner@example.invalid", "other@example.invalid")]
    import_emails(workspace.path, TwoAccounts())
    enable(workspace)
    assert run_automation(workspace.path, mailbox="owner@example.invalid").drafts == 1
    assert len(store.inbox_triage(workspace.path)) == 1
    assert run_automation(workspace.path, mailbox="other@example.invalid").drafts == 1


def test_restart_preserves_opt_in_and_does_not_repeat_completed_triage(workspace):
    insert(workspace)
    enable(workspace)
    run_automation(workspace.path)
    initialize_database(workspace.path)
    assert store.settings(workspace.path)["enabled"]
    assert run_automation(workspace.path).processed == 0
    assert len(list_drafts(workspace.path)) == 1


def test_mail_cleanup_removes_triage_and_history(workspace):
    class GmailMail(Mailbox):
        def fetch_new_emails(self):
            return [dict(super().fetch_new_emails()[0], provider="gmail", source_mailbox="owner@example.invalid")]
    import_emails(workspace.path, GmailMail())
    enable(workspace)
    run_automation(workspace.path)
    assert remove_gmail_emails_not_from(workspace.path, ("different@example.invalid",)) == 1
    assert not store.history(workspace.path) and not store.inbox_triage(workspace.path)


def test_history_does_not_open_a_discarded_reply(workspace, qapp):
    from app.reply.approval import reject_draft
    insert(workspace)
    enable(workspace)
    run_automation(workspace.path)
    reject_draft(workspace.path, list_drafts(workspace.path)[0].id)
    window = MainWindow(workspace.path, config=AppConfig(RunMode.MOCK, "mock"))
    control = window.automation_widget
    control.history_table.selectRow(0)
    assert not control.review_button.isEnabled()
    calls = []
    control.review_draft.connect(calls.append)
    control.review_selected()
    assert not calls
    window.close()


def test_background_refresh_preserves_unsaved_reply_and_cursor(workspace, qapp):
    insert(workspace)
    enable(workspace)
    run_automation(workspace.path)
    window = MainWindow(workspace.path, config=AppConfig(RunMode.MOCK, "mock"))
    window.navigate(2)
    editor = window.approval_widget.body_editor
    editor.setPlainText("My unsaved reply that I am still editing")
    cursor = editor.textCursor()
    cursor.setPosition(8)
    editor.setTextCursor(cursor)
    window._automation_complete()
    assert editor.toPlainText() == "My unsaved reply that I am still editing"
    assert editor.textCursor().position() == 8
    assert list_drafts(workspace.path)[0].body != editor.toPlainText()
    window.close()


def test_history_keeps_selected_event_when_new_actions_arrive(workspace, qapp):
    insert(workspace)
    enable(workspace)
    run_automation(workspace.path)
    window = MainWindow(workspace.path, config=AppConfig(RunMode.MOCK, "mock"))
    control = window.automation_widget
    control.history_table.selectRow(0)
    selected_id = control.selected_event()["id"]
    class AnotherMessage(Mailbox):
        def fetch_new_emails(self):
            return [dict(super().fetch_new_emails()[0], id="message-2", subject="Another question")]
    import_emails(workspace.path, AnotherMessage())
    run_automation(workspace.path)
    control.refresh_history()
    assert control.history_table.rowCount() == 2
    assert control.selected_event()["id"] == selected_id
    window.close()
