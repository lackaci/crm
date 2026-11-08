"""Sent originals leave Inbox only after confirmed delivery; history is retained."""
import time
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.db import inbox, replied
from app.db.database import initialize_database, open_database
from app.db.emails import get_email
from app.reply import delivery, review
from app.reply.approval import list_pending_drafts
from app.providers.email_base import DeliveryRejected, DeliveryUncertain
from app.ui.main_window import MainWindow
from app.ui.replied import RepliedEmailsWidget
from tests.test_desktop_delivery import mail, perform


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def test_sent_original_leaves_all_inbox_filters_and_keeps_exact_history(mail):
    assert inbox.counts(mail.path)['all'] == 1
    review.approve(mail.path, mail.draft_id, mail.config, 'Approved reply')
    assert replied.count(mail.path) == 0 and inbox.counts(mail.path)['pending'] == 1
    perform(mail)
    assert replied.count(mail.path) == 0 and inbox.counts(mail.path)['all'] == 1
    sent = perform(mail, send=True)
    assert inbox.messages(mail.path) == []
    assert all(value == 0 for value in inbox.counts(mail.path).values())
    for name in inbox.FILTERS:
        assert inbox.messages(mail.path, filter_name=name) == []
    history = replied.messages(mail.path)
    assert len(history) == 1 and history[0]['reply_id'] == sent['id']
    assert history[0]['incoming_body'] == 'Hello'
    assert history[0]['reply_body'] == 'Approved reply'
    assert history[0]['sender'] == mail.service.account
    assert history[0]['recipient'] == 'customer@example.invalid'
    assert get_email(mail.path, mail.email_id).status == 'done'
    with open_database(mail.path) as c:
        c.execute("INSERT INTO draft_replies(email_id,body) VALUES(?,'Old extra suggestion')", (mail.email_id,))
    assert list_pending_drafts(mail.path) == []
    initialize_database(mail.path)
    assert replied.count(mail.path) == 1 and inbox.messages(mail.path) == []


def test_sent_outbox_excludes_stale_unread_and_assignment_badges(mail):
    perform(mail)
    perform(mail, True)
    # A legacy or inconsistent source status cannot bring a confirmed send back.
    with open_database(mail.path) as c:
        c.execute("UPDATE emails SET status='new',needs_attention=1 WHERE id=?", (mail.email_id,))
    assert inbox.counts(mail.path)['unread'] == inbox.counts(mail.path)['assignment'] == 0
    assert replied.count(mail.path) == 1


def test_unknown_send_stays_active_until_reconciled(mail):
    perform(mail)
    mail.service.uncertain_send = True
    with pytest.raises(DeliveryUncertain):
        perform(mail, True)
    assert replied.count(mail.path) == 0 and inbox.counts(mail.path)['all'] == 1
    assert delivery.reconcile(mail.path, mail.draft_id, mail.config, mail.provider)['status'] == 'sent'
    assert replied.count(mail.path) == 1 and inbox.messages(mail.path) == []
    assert mail.service.send_calls == 1


def test_rejected_send_stays_in_inbox(mail):
    perform(mail)
    mail.service.reject_send = True
    with pytest.raises(DeliveryRejected):
        perform(mail, True)
    assert replied.count(mail.path) == 0 and inbox.counts(mail.path)['all'] == 1


def test_history_search_includes_sent_body_and_escapes_wildcards(mail):
    perform(mail)
    perform(mail, True)
    for search in ('customer@example.invalid', 'question', 'hello', 'approved reply'):
        assert replied.count(mail.path, search) == 1
        assert len(replied.messages(mail.path, search)) == 1
    assert replied.count(mail.path, '%') == replied.count(mail.path, '_') == 0
    assert replied.get_reply(mail.path, 'missing') is None


def test_gui_send_moves_message_to_visible_reply_menu(mail, qapp):
    window = MainWindow(mail.path, config=mail.config, email_provider=mail.provider)
    window.show()
    assert window.inbox_widget.email_table.rowCount() == 1
    assert not window.navigation.item(7).isHidden()
    window.navigate(2)
    widget = window.approval_widget
    widget.select_draft(mail.draft_id)
    QTest.mouseClick(widget.review_approve_button, Qt.MouseButton.LeftButton)
    assert replied.count(mail.path) == 0
    QTest.mouseClick(widget.send_reply_button, Qt.MouseButton.LeftButton)
    deadline = time.monotonic() + 10
    while widget.delivery_task is not None and time.monotonic() < deadline:
        qapp.processEvents()
        QTest.qWait(10)
    assert widget.delivery_task is None
    assert mail.service.send_calls == 1
    assert window.inbox_widget.email_table.rowCount() == 0
    assert window.inbox_widget.unmatched_table.rowCount() == 0
    assert not window.inbox_widget.create_draft_button.isEnabled()
    assert not window.inbox_widget.write_reply_button.isEnabled()
    assert window.navigation.item(7).text() == 'Replied (1)'
    assert '0 across' in window.inbox_widget.unread_label.text()
    item = window.navigation.item(7)
    QTest.mouseClick(window.navigation.viewport(), Qt.MouseButton.LeftButton,
                     pos=window.navigation.visualItemRect(item).center())
    assert window.pages.currentWidget() is window.replied_widget
    assert 'Hello' in window.replied_widget.incoming_view.toPlainText()
    assert window.replied_widget.reply_view.toPlainText() == 'Approved reply'
    assert window.replied_widget.reply_view.isReadOnly()
    window.close()
    restarted = MainWindow(mail.path, config=mail.config, email_provider=mail.provider)
    restarted.navigate(7)
    assert restarted.replied_widget.reply_table.rowCount() == 1
    restarted.close()


def test_history_pagination_and_empty_search_clear_reader(mail, qapp):
    perform(mail)
    perform(mail, True)
    with open_database(mail.path) as c:
        original = dict(c.execute('SELECT * FROM desktop_outbox').fetchone())
        for number in range(101):
            draft = c.execute("INSERT INTO draft_replies(email_id,body,status) VALUES(?,'Other sent reply','approved')", (mail.email_id,)).lastrowid
            values = {**original, 'id': f'copy-{number:03d}', 'draft_id': draft, 'content_hash': f'copy-hash-{number}',
                      'rfc_message_id': f'<copy-{number}@example.invalid>', 'updated_at': f'2026-11-08 12:{number // 60:02d}:{number % 60:02d}'}
            c.execute('INSERT INTO desktop_outbox(' + ','.join(values) + ') VALUES(' + ','.join('?' for _ in values) + ')', list(values.values()))
    widget = RepliedEmailsWidget(mail.path)
    assert widget.reply_table.rowCount() == 100 and widget.next_button.isEnabled()
    first_page = {widget.reply_table.item(row, 0).data(Qt.ItemDataRole.UserRole) for row in range(100)}
    widget.next_button.click()
    assert widget.reply_table.rowCount() == 2 and not widget.next_button.isEnabled()
    assert widget.selected_reply_id not in first_page
    widget.search_input.setText('no-match-for-this-search')
    QTest.qWait(250)
    assert widget.reply_table.rowCount() == 0
    assert widget.reply_view.toPlainText() == widget.incoming_view.toPlainText() == ''
    assert widget.offset == 0
    widget.close()
