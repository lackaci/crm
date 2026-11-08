"""Desktop incremental Gmail and incoming queue acceptance checks, offline."""
import base64
from contextlib import closing
from datetime import datetime, timezone
import sqlite3
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.db.database import initialize_database, open_database
from app.db.customers import add_customer
from app.db.emails import import_emails, get_email, set_email_status
from app.db import inbox as store
from app.db.connections import set_mail_paused
from app.email_sync import synchronize_email
from app.providers.gmail import GmailProvider
from app.providers.factory import BackgroundGmailImport
from app.ui.inbox import CustomerInboxWidget


class ApiError(Exception):
    def __init__(self, status):
        self.resp = SimpleNamespace(status=status)
        super().__init__("PRIVATE-TOKEN-MESSAGE-BODY")


class Gmail:
    """Only read endpoints exist; any attempted write fails the test."""
    def __init__(self):
        self.calls = []
        self.address, self.cursor = "owner@example.invalid", "100"
        self.full = {None: {"messages": [{"id": "m1"}, {"id": "m2"}]}}
        self.changes = {None: {"historyId": "110", "history": []}}
        self.failures = {}
        self.payloads = {key: self.message(key) for key in ("m1", "m2", "m3")}

    def message(self, key, sender="known@example.invalid", body="When are you open?"):
        return {"id": key, "threadId": "thread-" + key, "internalDate": str(int(time.time() * 1000)),
                "payload": {"headers": [{"name": "From", "value": "Customer <" + sender + ">"},
                                         {"name": "Subject", "value": "Hours " + key}],
                            "mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()}}}

    def users(self):
        return self

    def messages(self):
        return SimpleNamespace(list=lambda **options: self.request("list", options),
                               get=lambda **options: self.request("get", options))

    def history(self):
        return SimpleNamespace(list=lambda **options: self.request("history", options))

    def getProfile(self, **options):
        return self.request("profile", options)

    def request(self, operation, options):
        owner = self
        class Request:
            def execute(self, num_retries=0):
                assert num_retries == 0
                owner.calls.append((operation, options))
                key = options.get("id", operation)
                if key in owner.failures:
                    raise owner.failures[key]
                if operation == "profile":
                    return {"emailAddress": owner.address, "historyId": owner.cursor}
                if operation == "list":
                    return owner.full[options.get("pageToken")]
                if operation == "history":
                    return owner.changes[options.get("pageToken")]
                return owner.payloads[key]
        return Request()


@pytest.fixture
def mail(tmp_path):
    path = tmp_path / "desktop.sqlite"
    initialize_database(path)
    customer = add_customer(path, "Customer", "known@example.invalid")
    service = Gmail()
    provider = GmailProvider(service=service, expected_mailbox=service.address, newer_than_days=None)
    return SimpleNamespace(path=path, customer=customer, service=service, provider=provider, storage=tmp_path / "files")


def sync(mail, **options):
    return synchronize_email(mail.path, mail.provider, storage_root=mail.storage, **options)


def added(*keys):
    return {"historyId": "120", "history": [{"messagesAdded": [{"message": {"id": key}} for key in keys]}]}


def test_incremental_refresh_skips_duplicate_payloads_and_reports_progress(mail):
    progress = []
    result = sync(mail, progress=progress.append)
    assert result.imported == 2 and result.failed == 0
    assert "Importing... 0/2" in progress and "Importing... 2/2" in progress
    mail.service.changes[None] = added("m2", "m3")
    mail.service.calls.clear()
    result = sync(mail)
    assert result.imported == result.existing == 1 and result.failed == 0
    assert not any(operation == "list" for operation, _ in mail.service.calls)
    assert [options["id"] for operation, options in mail.service.calls if operation == "get"] == ["m3"]
    history = next(options for operation, options in mail.service.calls if operation == "history")
    assert history["startHistoryId"] == "100"
    with closing(open_database(mail.path)) as connection:
        assert connection.execute("SELECT history_id FROM desktop_gmail_sync").fetchone()[0] == "120"


def test_partial_failure_keeps_good_messages_and_retries_failed_id(mail):
    mail.service.failures["m1"] = ValueError("PRIVATE-TOKEN")
    result = sync(mail)
    assert (result.imported, result.failed, result.remaining) == (1, 1, 1)
    assert len(store.messages(mail.path)) == 1
    assert "PRIVATE" not in result.error
    del mail.service.failures["m1"]
    mail.provider = GmailProvider(service=mail.service, expected_mailbox=mail.service.address, newer_than_days=None)
    result = sync(mail)
    assert (result.imported, result.failed, result.remaining) == (1, 0, 0)
    assert len(store.messages(mail.path)) == 2


def test_invalid_message_does_not_roll_back_other_messages(mail):
    mail.service.payloads["m1"]["payload"]["headers"][0]["value"] = ""
    result = sync(mail)
    assert result.imported == 1 and result.failed == 1
    assert store.messages(mail.path)[0]["remote_message_id"] == "m2"


def test_history_expiry_rechecks_lookback_without_redownloading_existing(mail):
    assert sync(mail).imported == 2
    mail.service.failures["history"] = ApiError(404)
    mail.service.full[None]["messages"].append({"id": "m3"})
    mail.service.calls.clear()
    result = sync(mail)
    assert result.imported == 1 and result.existing == 2
    assert [options["id"] for operation, options in mail.service.calls if operation == "get"] == ["m3"]


def test_capped_batches_retain_discovered_backlog(mail):
    mail.provider.max_messages = 1
    first, second = sync(mail), sync(mail)
    assert (first.imported, first.remaining) == (1, 1)
    assert (second.imported, second.remaining) == (1, 0)


def test_full_and_history_pagination_and_retry_after_page_failure(mail):
    mail.service.full = {None: {"messages": [{"id": "m1"}], "nextPageToken": "next"},
                         "next": {"messages": [{"id": "m2"}]}}
    assert sync(mail).imported == 2
    mail.service.changes = {None: {"history": [], "historyId": "115", "nextPageToken": "next"},
                            "next": added("m3")}
    assert sync(mail).imported == 1
    calls = [options for operation, options in mail.service.calls if operation == "history"]
    assert calls[0]["startHistoryId"] == calls[1]["startHistoryId"] == "100"
    assert calls[1]["pageToken"] == "next"


def test_wrong_mailbox_and_pause_prevent_import(mail):
    mail.service.address = "wrong@example.invalid"
    assert sync(mail).error
    assert store.messages(mail.path) == []
    mail.service.calls.clear()
    set_mail_paused(mail.path, True)
    assert "paused" in sync(mail).error
    assert not mail.service.calls


def test_discovery_failure_resumes_saved_page_without_losing_queued_ids(mail):
    class FailingPage(dict):
        broken = True
        def __getitem__(self, key):
            if key == "next" and self.broken:
                raise OSError("PRIVATE")
            return super().__getitem__(key)
    pages = FailingPage({None: {"messages": [{"id": "m1"}], "nextPageToken": "next"},
                         "next": {"messages": [{"id": "m2"}]}})
    mail.service.full = pages
    assert sync(mail).error
    with closing(open_database(mail.path)) as connection:
        assert connection.execute("SELECT page_token FROM desktop_gmail_sync").fetchone()[0] == "next"
        assert connection.execute("SELECT COUNT(*) FROM desktop_gmail_queue").fetchone()[0] == 1
    pages.broken = False
    mail.service.calls.clear()
    assert sync(mail).imported == 2
    assert next(options for op, options in mail.service.calls if op == "list")["pageToken"] == "next"


def test_quota_failure_stops_batch_and_leaves_work_for_later(mail):
    mail.service.failures["m1"] = ApiError(429)
    result = sync(mail)
    assert result.failed == 1 and result.remaining == 2
    assert [options["id"] for op, options in mail.service.calls if op == "get"] == ["m1"]
    del mail.service.failures["m1"]
    assert sync(mail).imported == 2


def test_changed_sender_scope_backfills_and_history_enforces_it(mail):
    mail.provider.allowed_senders = ("known@example.invalid",)
    assert sync(mail).imported == 2
    mail.service.payloads["m3"] = mail.service.message("m3", sender="other@example.invalid")
    mail.service.changes[None] = added("m3")
    assert sync(mail).imported == 0
    mail.provider.allowed_senders = ()
    mail.service.full[None]["messages"].append({"id": "m3"})
    assert sync(mail).imported == 1


def test_internal_arrival_date_wins_over_backdated_header(mail):
    mail.service.payloads["m1"]["payload"]["headers"].append({"name": "Date", "value": "Sat, 1 Jan 2000 10:00:00 +0000"})
    assert sync(mail).imported == 2
    assert all(row["received_ts"] > 1700000000 for row in store.messages(mail.path))


def test_outgoing_and_spam_events_do_not_enter_incoming_queue(mail):
    mail.service.payloads["m1"]["labelIds"] = ["SENT"]
    mail.service.payloads["m2"]["labelIds"] = ["SPAM"]
    assert sync(mail).imported == 0
    assert store.messages(mail.path) == []


@pytest.mark.parametrize("error, expected", [(ApiError(401), "reconnect"), (ApiError(429), "quota"),
                                             (OSError("PRIVATE"), "internet")])
def test_errors_are_readable_actionable_and_do_not_expose_payloads(mail, error, expected):
    mail.service.failures["profile"] = error
    result = sync(mail)
    assert expected in result.error and "PRIVATE" not in result.error


def test_scheduled_and_manual_refresh_share_cursor(mail):
    assert sync(mail).imported == 2
    result = synchronize_email(mail.path, BackgroundGmailImport(mail.provider))
    assert result.imported == 0
    assert any(operation == "history" for operation, _ in mail.service.calls)


def test_migration_preserves_legacy_dates_content_and_read_state(tmp_path):
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE emails(id INTEGER PRIMARY KEY,from_name TEXT,from_email TEXT,subject TEXT,body TEXT,received_at TEXT,status TEXT)")
        connection.execute("INSERT INTO emails VALUES(1,'A','a@example.invalid','Refund','I demand a refund','Tue, 27 Oct 2026 10:00:00 +0000','seen')")
    initialize_database(path)
    initialize_database(path)
    row = store.messages(path)[0]
    assert row["body"] == "I demand a refund" and row["status"] == "seen"
    assert row["attention"] == 1 and row["received_ts"] > 0
    assert store.counts(path, "refund")["all"] == 1


def seed_inbox(mail):
    class Messages:
        def fetch_new_emails(self):
            return [dict(id="old", from_name="Customer", from_email="known@example.invalid", subject="Hours",
                         body="When are you open?", received_at="2026-11-01T12:00:00Z"),
                    dict(id="new", from_name="Unknown", from_email="new@example.invalid", subject="Refund request",
                         body="I demand a refund", received_at="Mon, 02 Nov 2026 12:00:00 +0000")]
    import_emails(mail.path, Messages(), storage_root=mail.storage)


def test_filters_search_dates_pending_and_read_state(mail):
    seed_inbox(mail)
    rows = store.messages(mail.path)
    assert [row["external_id"] for row in rows] == ["new", "old"]
    assert store.counts(mail.path) == {"all": 2, "unread": 2, "assignment": 1, "attention": 1, "pending": 0}
    assert store.messages(mail.path, filter_name="attention")[0]["external_id"] == "new"
    assert len(store.messages(mail.path, search="ew@example")) == 1
    assert len(store.messages(mail.path, search="refund")) == 1
    assert len(store.messages(mail.path, search='" OR *')) == 0
    with closing(open_database(mail.path)) as connection, connection:
        connection.execute("INSERT INTO draft_replies(email_id,body) VALUES(?, 'Approved answer')", (rows[1]["id"],))
    assert store.counts(mail.path)["pending"] == 1
    assert store.messages(mail.path, filter_name="pending")[0]["external_id"] == "old"


def test_search_and_paging_over_thousands_use_fts_index(mail):
    with closing(open_database(mail.path)) as connection, connection:
        connection.executemany("INSERT INTO emails(from_name,from_email,subject,body,received_ts) VALUES('Sender','s@example.invalid',?,?,?)",
            [(f"Message {number}", "unique-needle" if number == 1234 else "Common text", number) for number in range(4000)])
        plan = " ".join(str(tuple(row)) for row in connection.execute("EXPLAIN QUERY PLAN SELECT rowid FROM desktop_email_search WHERE desktop_email_search MATCH ?", ('"unique-needle"',)))
        assert "VIRTUAL TABLE INDEX" in plan
    start = time.monotonic()
    assert store.counts(mail.path, "unique-needle")["all"] == 1
    assert store.messages(mail.path, search="unique-needle")[0]["subject"] == "Message 1234"
    assert time.monotonic() - start < 2
    first, second = store.messages(mail.path), store.messages(mail.path, offset=100)
    assert len(first) == len(second) == 100
    assert not {row["id"] for row in first} & {row["id"] for row in second}


def test_search_index_tracks_edits_and_deletes(mail):
    seed_inbox(mail)
    with closing(open_database(mail.path)) as connection, connection:
        connection.execute("UPDATE emails SET body='different phrase',subject='Updated' WHERE external_id='new'")
    assert store.counts(mail.path, "refund")["all"] == 0
    assert store.counts(mail.path, "different phrase")["all"] == 1
    with closing(open_database(mail.path)) as connection, connection:
        connection.execute("DELETE FROM emails WHERE external_id='new'")
    assert store.counts(mail.path, "different phrase")["all"] == 0


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def test_inbox_opens_all_and_unknown_message_can_be_read_and_linked_in_place(mail, qapp):
    seed_inbox(mail)
    widget = CustomerInboxWidget(mail.path, storage_root=mail.storage)
    assert widget.email_table.rowCount() == 2
    assert "Unread (2)" == widget.filter_buttons["unread"].text()
    widget.email_table.selectRow(0)
    email_id = widget.selected_email_id
    assert get_email(mail.path, email_id).status == "seen"
    assert "Customer: Needs assignment" in widget.message_details.text()
    assert "refund" in widget.email_preview.toPlainText()
    assert not widget.assignment_panel.isHidden()
    assert widget.create_draft_button.isEnabled()
    assert widget.create_draft_button.text() == "Link customer first"
    widget.assign_button.click()
    assert get_email(mail.path, email_id).customer_id == mail.customer
    assert widget.create_draft_button.isEnabled()
    assert store.counts(mail.path)["assignment"] == 0
    widget.close()


def test_unread_filter_keeps_reader_until_refresh_and_never_reopens_done(mail, qapp):
    seed_inbox(mail)
    widget = CustomerInboxWidget(mail.path)
    widget.choose_filter("unread")
    widget.email_table.selectRow(0)
    email_id = widget.selected_email_id
    assert store.counts(mail.path)["unread"] == 1
    assert "refund" in widget.email_preview.toPlainText()
    widget.refresh()
    assert widget.email_table.rowCount() == 1
    set_email_status(mail.path, email_id, "done")
    widget.open_message(email_id)
    assert get_email(mail.path, email_id).status == "done"
    widget.close()


def test_search_is_debounced_and_filters_database_not_loaded_rows(mail, qapp):
    seed_inbox(mail)
    widget = CustomerInboxWidget(mail.path)
    widget.search_input.setText("refund")
    QTest.qWait(250)
    assert widget.email_table.rowCount() == 1
    assert widget.email_table.item(0, 0).text() == "Refund request"
    widget.search_input.setText("no matching text")
    QTest.qWait(250)
    assert widget.email_table.rowCount() == 0 and not widget.create_draft_button.isEnabled()
    widget.close()


def test_refresh_is_nonblocking_progress_visible_and_duplicates_blocked(mail, qapp, monkeypatch):
    import threading
    release = threading.Event()
    entered = threading.Event()
    original = mail.provider.import_incrementally
    def slow(*args, **options):
        entered.set()
        release.wait(3)
        return original(*args, **options)
    monkeypatch.setattr(mail.provider, "import_incrementally", slow)
    widget = CustomerInboxWidget(mail.path, provider=mail.provider, storage_root=mail.storage)
    widget.start_import()
    task = widget.import_task
    widget.start_import()
    assert widget.import_task is task and not widget.import_button.isEnabled()
    assert entered.wait(1)
    qapp.processEvents()
    assert widget.search_input.isEnabled()
    release.set()
    deadline = time.monotonic() + 6
    while widget.import_task is not None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert widget.import_task is None
    assert "Imported 2 new, 0 already existed, 0 failed" in widget.status_label.text()
    assert widget.import_button.isEnabled() and widget.email_table.rowCount() == 2
    widget.close()


def test_visible_gmail_button_opens_setup_then_imports_automatically(mail, qapp, tmp_path, monkeypatch):
    from app.config import AppConfig, RunMode
    from app.ui.main_window import MainWindow
    client = tmp_path / "private-client.json"
    client.write_text("{}")
    window = MainWindow(mail.path, storage_root=mail.storage, config=AppConfig(RunMode.MOCK, "mock"))
    window.show()
    qapp.processEvents()
    inbox = window.inbox_widget
    assert inbox.import_button.text() == "Import from Gmail"
    QTest.mouseClick(inbox.import_button, Qt.MouseButton.LeftButton)
    assert window.pages.currentWidget() is window.settings_widget
    control = window.settings_widget.gmail_widget
    assert "automatically" in control.status_label.text()
    assert not control.write_checkbox.isChecked() and not control.send_checkbox.isChecked()
    control.account_input.setText(mail.provider.expected_mailbox)
    control.credentials_input.setText(str(client))
    monkeypatch.setattr("app.ui.gmail_connection.email_provider", lambda config: mail.provider)
    QTest.mouseClick(control.connect_button, Qt.MouseButton.LeftButton)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        qapp.processEvents()
        if inbox.email_table.rowCount() == 2 and inbox.import_task is None and control.task is None:
            break
        time.sleep(0.01)
    assert inbox.email_table.rowCount() == 2
    assert window.pages.currentWidget() is inbox
    assert "Imported 2 new" in inbox.status_label.text()
    assert inbox.import_button.text() == "Import from Gmail" and inbox.import_button.isEnabled()
    assert not window.config.sending_enabled
    window.close()


def test_manual_import_clears_filters_even_when_no_new_mail(mail, qapp):
    mail.service.payloads["m2"] = mail.service.message("m2", sender="unknown@example.invalid")
    assert sync(mail).imported == 2
    widget = CustomerInboxWidget(mail.path, provider=mail.provider, storage_root=mail.storage)
    widget.set_customer(mail.customer)
    widget.choose_filter("unread")
    widget.search_input.setText("does not match any email")
    QTest.qWait(250)
    assert widget.email_table.rowCount() == 0
    widget.import_button.click()
    deadline = time.monotonic() + 6
    while widget.import_task is not None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert widget.import_task is None
    assert widget.search_input.text() == ""
    assert widget.scope_combo.currentIndex() == 0 and widget.filter_name == "all"
    assert widget.email_table.rowCount() == 2
    assert "No new emails matched" in widget.status_label.text()
    assert "Change import options" in widget.status_label.text()
    widget.close()


def test_scheduled_import_keeps_existing_search_and_filter(mail, qapp):
    assert sync(mail).imported == 2
    widget = CustomerInboxWidget(mail.path, provider=mail.provider)
    widget.choose_filter("unread")
    widget.search_input.setText("Hours")
    QTest.qWait(250)
    widget.start_import(automatic=True)
    deadline = time.monotonic() + 6
    while widget.import_task is not None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert widget.import_task is None
    assert widget.search_input.text() == "Hours" and widget.filter_name == "unread"
    widget.close()


def test_sender_restriction_is_visible_and_options_open_without_changing_it(mail, qapp):
    from app.config import AppConfig, RunMode
    from app.ui.main_window import MainWindow
    mail.provider.allowed_senders = ("known@example.invalid",)
    mail.provider.newer_than_days = 30
    window = MainWindow(mail.path, email_provider=mail.provider, storage_root=mail.storage,
                        config=AppConfig(RunMode.DRY_RUN, "gmail", test_mailbox=mail.provider.expected_mailbox))
    inbox = window.inbox_widget
    assert "1 allowed sender" in inbox.import_scope_label.text()
    assert "Last 30 days" in inbox.import_scope_label.text()
    inbox.import_options_button.click()
    assert window.pages.currentWidget() is window.settings_widget
    assert "all senders" in window.settings_widget.gmail_widget.status_label.text()
    assert mail.provider.allowed_senders == ("known@example.invalid",)
    window.close()
