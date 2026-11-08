"""A beginner-friendly review screen for generated reply drafts."""

import sqlite3
import json
from pathlib import Path
from PySide6.QtCore import Signal, Qt

from PySide6.QtWidgets import (
    QFormLayout,
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.db.emails import get_email
from app.db.faqs import list_approved_faqs
from app.providers.email_base import EmailDeliveryError, EmailProvider
from app.providers.email_mock import MockEmailProvider
from app.reply.approval import (
    edit_draft,
    list_pending_drafts,
    reject_draft,
)
from app.reply import delivery
from app.db.connections import mail_paused
from .background import BackgroundTask


class ApprovalQueueWidget(QWidget):
    """Display pending drafts and let a reviewer edit, reject, or approve them.

    The widget deliberately calls application/database helpers instead of
    containing SQL.  The provider is injected so tests can use a safe fake.
    """

    queue_changed = Signal()
    regenerate_requested = Signal(int)

    def __init__(
        self,
        database_path: Path,
        provider: EmailProvider | None = None,
        parent: QWidget | None = None,
        email_provider: EmailProvider | None = None,
        # M10 keeps delivery disabled even when older callers pass True.
        sending_enabled: bool = False,
    ) -> None:
        super().__init__(parent)
        self.database_path = database_path
        self.provider = provider or email_provider or MockEmailProvider()
        self.sending_enabled = False
        self.delivery_config = None
        self.delivery_task = None
        self.demo_mode = False
        self.review_workflow = False
        self._drafts = []
        self._selected_id: int | None = None

        self.draft_list = QListWidget()
        self.draft_list.setWordWrap(True)
        self.draft_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.draft_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.draft_list.setAccessibleName("Pending drafts")
        self.draft_list.currentRowChanged.connect(self._select_row)
        # Keep the empty state outside the list so it remains visible when
        # QListWidget has no rows.
        self.empty_state_label = QLabel("No pending drafts.")
        self.empty_state_label.setObjectName("emptyStateLabel")

        self.recipient_label = QLabel("—")
        self.subject_label = QLabel("—")
        self.incoming_label = QPlainTextEdit("No draft selected.")
        self.incoming_label.setReadOnly(True)
        self.incoming_label.setMinimumHeight(80)
        self.incoming_label.setMaximumHeight(140)
        self.source_faq_label = QPlainTextEdit()
        self.source_faq_label.setReadOnly(True)
        self.source_faq_label.setMinimumHeight(65)
        self.source_faq_label.setMaximumHeight(110)
        self.body_editor = QTextEdit()
        self.body_editor.setPlaceholderText("Select a draft to edit its reply.")
        # These aliases make the most important controls easy to discover in
        # a small prototype and keep tests/readers from depending on layout.
        self.body_input = self.body_editor
        self.list_widget = self.draft_list

        details = QFormLayout()
        details.addRow("Recipient", self.recipient_label)
        details.addRow("Subject", self.subject_label)
        details.addRow("Incoming email", self.incoming_label)
        details.addRow("Saved answer", self.source_faq_label)
        details.addRow("Proposed reply", self.body_editor)

        self.edit_button = QPushButton("Save changes")
        self.reject_button = QPushButton("Discard reply")
        self.copy_button = QPushButton("Copy reply")
        self.copy_button.setObjectName("primaryButton")
        self.copy_button.clicked.connect(self.copy_reply)
        self.copy_button.setEnabled(False)
        self.approve_button = QPushButton("Send (unavailable)")
        self.gmail_draft_button = QPushButton("Create Gmail draft")
        self.check_delivery_button = QPushButton("Check Gmail status")
        self.cancel_gmail_button = QPushButton("Cancel Gmail draft")
        self.gmail_draft_button.clicked.connect(self.create_gmail_draft)
        self.check_delivery_button.clicked.connect(self.check_delivery)
        self.cancel_gmail_button.clicked.connect(self.cancel_gmail_draft)
        self.edit_button.clicked.connect(self.save_edit)
        self.reject_button.clicked.connect(self.reject)
        self.approve_button.clicked.connect(self.approve)
        self.approve_button.setEnabled(False)
        self.approve_button.setToolTip("Sending is disabled. You can review, edit, and copy the reply.")

        actions = QHBoxLayout()
        actions.addWidget(self.edit_button)
        actions.addWidget(self.reject_button)
        actions.addWidget(self.copy_button)
        actions.addStretch()

        detail_page = QWidget()
        detail_layout = QVBoxLayout(detail_page)
        detail_layout.addLayout(details)
        detail_layout.addLayout(actions)
        gmail_actions = QHBoxLayout()
        gmail_actions.addWidget(self.gmail_draft_button)
        gmail_actions.addWidget(self.approve_button)
        self.review_approve_button = QPushButton("Approve reply")
        self.send_reply_button = QPushButton("Send reply")
        self.send_reply_button.setObjectName("primaryButton")
        self.regenerate_button = QPushButton("Regenerate AI reply")
        self.review_approve_button.clicked.connect(self.approve_reply)
        self.send_reply_button.clicked.connect(self.send_reply)
        self.regenerate_button.clicked.connect(self.request_regeneration)
        for button in (self.regenerate_button, self.review_approve_button, self.send_reply_button):
            gmail_actions.addWidget(button)
            button.hide()
        gmail_actions.addStretch()
        detail_layout.addLayout(gmail_actions)
        recovery_actions = QHBoxLayout()
        recovery_actions.addWidget(self.check_delivery_button)
        recovery_actions.addWidget(self.cancel_gmail_button)
        recovery_actions.addStretch()
        detail_layout.addLayout(recovery_actions)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.draft_list)
        splitter.addWidget(detail_page)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([230, 650])
        self.status_label = QLabel("Review and copy replies here. Sending is disabled.")
        self.status_label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.empty_state_label)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.status_label)
        self.body_editor.textChanged.connect(self._review_controls)
        self.refresh()

    def enable_review_workflow(self):
        self.review_workflow = True
        self.update_delivery_controls()

    def _review_controls(self):
        if not self.review_workflow:
            return
        from app.reply import review
        selected = self._selected_id is not None
        if selected:
            from app.db.database import open_database
            with open_database(self.database_path) as connection:
                stored = connection.execute("SELECT body FROM draft_replies WHERE id=?", (self._selected_id,)).fetchone()
                if stored and stored['body'] != self.body_editor.toPlainText():
                    connection.execute("DELETE FROM desktop_reply_approvals WHERE draft_id=?", (self._selected_id,))
        busy = self.delivery_task is not None
        row = delivery.outbox_for_draft(self.database_path, self._selected_id) if selected else None
        editable = not row or row['status'] in {'failed', 'cancelled'}
        approved = False
        if selected and self.delivery_config:
            try:
                review.require_approved(self.database_path, self._selected_id, self.delivery_config, visible_body=self.body_editor.toPlainText())
                approved = True
            except ValueError:
                pass
        self.gmail_draft_button.hide()
        self.approve_button.hide()
        for button in (self.regenerate_button, self.review_approve_button, self.send_reply_button):
            button.show()
        self.review_approve_button.setEnabled(selected and not busy and bool(self.body_editor.toPlainText().strip()))
        self.review_approve_button.setText("Approved" if approved else "Approve reply")
        self.regenerate_button.setEnabled(selected and editable and not busy)
        available = bool(self.delivery_config and self.delivery_config.provider == 'gmail' and not self.demo_mode
                         and self.delivery_config.gmail_write_enabled and self.delivery_config.sending_enabled)
        permitted = self.recipient_label.text().strip().lower() in self.delivery_config.send_allowlist if self.delivery_config else False
        certain = not row or row['status'] in {'failed','cancelled','drafted'}
        self.send_reply_button.setEnabled(approved and available and permitted and not busy and certain and not mail_paused(self.database_path))
        self.send_reply_button.setToolTip("Approve the exact reply first. Enable Gmail sending and allow this recipient in Setup. Uncertain deliveries must be checked before retrying.")

    def approve_reply(self):
        if not self._selected_id or not self.delivery_config or self.delivery_task is not None:
            return
        try:
            from app.db.drafts import get_draft
            from app.reply import review
            body = self.body_editor.toPlainText().strip()
            if get_draft(self.database_path, self._selected_id).body != body:
                edit_draft(self.database_path, self._selected_id, body)
            if self.body_editor.toPlainText() != body:
                self.body_editor.setPlainText(body)
            review.approve(self.database_path, self._selected_id, self.delivery_config, body)
            self.status_label.setText(f"Approved for {self.recipient_label.text()} from {self.delivery_config.test_mailbox or 'offline workspace'}. Click Send reply when ready. Edits require new approval.")
            self._review_controls()
            self.queue_changed.emit()
        except (ValueError, sqlite3.Error) as error:
            self.status_label.setText(str(error))

    def send_reply(self):
        if not self._selected_id or self.demo_mode or self.delivery_task is not None:
            return
        try:
            from app.reply import review
            preview = review.require_approved(self.database_path, self._selected_id, self.delivery_config,
                                             visible_body=self.body_editor.toPlainText())
            identifier, config, provider = self._selected_id, self.delivery_config, self.provider
            self._start_delivery(lambda: review.send_approved(self.database_path, identifier, preview['content_hash'], config, provider))
        except (ValueError, sqlite3.Error) as error:
            self.status_label.setText(str(error))

    def request_regeneration(self):
        if self._selected_id:
            from app.db.drafts import get_draft
            if self.body_editor.toPlainText() != get_draft(self.database_path, self._selected_id).body:
                self.status_label.setText("Save your edits before regenerating. Regeneration replaces the saved reply and removes approval.")
                return
            self.regenerate_requested.emit(self._selected_id)

    def set_connection(self, config, provider):
        self.delivery_config, self.provider = config, provider
        self.sending_enabled = bool(config.sending_enabled and not self.demo_mode)
        self.update_delivery_controls()

    def update_delivery_controls(self):
        available = bool(self.delivery_config and self.delivery_config.provider == "gmail" and not self.demo_mode)
        paused = mail_paused(self.database_path)
        busy = self.delivery_task is not None
        row = delivery.outbox_for_draft(self.database_path, self._selected_id) if self._selected_id else None
        state = row["status"] if row else None
        write = bool(available and self.delivery_config.gmail_write_enabled and not paused and not busy and self._selected_id)
        self.gmail_draft_button.setVisible(available)
        self.check_delivery_button.setVisible(available)
        self.cancel_gmail_button.setVisible(available)
        self.gmail_draft_button.setEnabled(write and state in {None, "failed", "cancelled"})
        self.check_delivery_button.setEnabled(write and row is not None)
        self.cancel_gmail_button.setEnabled(write and state == "drafted")
        permitted = False
        if write and self.sending_enabled and state == "drafted":
            try:
                preview = delivery.snapshot(self.database_path, self._selected_id, self.delivery_config, self.body_editor.toPlainText())
                permitted = preview["recipient"] in self.delivery_config.send_allowlist
            except ValueError:
                pass
        self.approve_button.setText("Approve and send" if available else "Send (unavailable)")
        self.approve_button.setEnabled(permitted)
        self.approve_button.setToolTip("Create the Gmail draft first. Sending requires Live mode, an allowed recipient, and confirmation." if available else "Sending is disabled. Connect Gmail in Settings in your regular workspace.")
        editable = not busy and state in {None, "failed", "cancelled"}
        self.edit_button.setEnabled(self._selected_id is not None and editable)
        self.reject_button.setEnabled(self._selected_id is not None and editable)
        self.body_editor.setReadOnly(not editable)
        if row:
            labels = {"drafted": "Gmail draft created. Review it here, then approve sending if enabled.",
                "unknown": "Gmail outcome is uncertain. Check status; no automatic resend will occur.",
                "creating": "Previous Gmail operation needs a status check before another attempt.",
                "sending": "Previous send needs a status check. It will not be resent automatically.",
                "failed": "Gmail rejected the operation. Fix the connection, then review again.",
                "cancelled": "Gmail draft cancelled. You can edit the local reply again."}
            if state in labels:
                self.status_label.setText(labels[state])
        elif available:
            if paused:
                self.status_label.setText("Mail activity is paused. Resume it in Gmail settings.")
            elif not self.delivery_config.gmail_write_enabled:
                self.status_label.setText("Enable Gmail draft permissions in Settings to create a draft. You can still edit and copy replies.")
            elif self._selected_id is None:
                self.status_label.setText("Select a reply to create a Gmail draft.")
            else:
                self.status_label.setText("Save any edits, then create a Gmail draft. " +
                    ("Review and approve sending after that." if self.sending_enabled else "Live sending is disabled."))
        if self.review_workflow:
            self._review_controls()
            if not row and self._selected_id:
                self.status_label.setText("Review or edit this reply, Approve reply, then Send reply. Sending permissions and allowed recipients are configured in Setup.")

    def _confirmed(self, title, preview, send=False):
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(("Send this exact reply now?" if send else "Create this reply as an unsent draft in Gmail?") +
            f"\n\nFrom: {preview['sender']}\nTo: {preview['recipient']}\nSubject: {preview['subject']}")
        box.setDetailedText(preview["body"])
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _start_delivery(self, operation):
        if self.delivery_task is not None:
            return
        self.delivery_task = BackgroundTask(operation, self)
        self.delivery_task.succeeded.connect(self._delivery_succeeded)
        self.delivery_task.failed.connect(self._delivery_failed)
        self.delivery_task.finished.connect(self._delivery_finished)
        self.update_delivery_controls()
        self.status_label.setText("Working with Gmail…")
        self.delivery_task.start()

    def _delivery_succeeded(self, result):
        self.refresh()
        if result and result.get("status") == "sent":
            self.status_label.setText("Gmail accepted the reply. It was sent once. Find it in the Replied menu.")
        elif result and result.get("status") in {"unknown", "creating", "sending"}:
            self.status_label.setText("No confirmed Gmail result yet. Check again later; nothing was resent.")

    def _delivery_failed(self, message):
        self.status_label.setText(message)

    def _delivery_finished(self):
        message = self.status_label.text()
        old, self.delivery_task = self.delivery_task, None
        old.deleteLater()
        self.update_delivery_controls()
        self.status_label.setText(message)

    def create_gmail_draft(self):
        self._request_delivery(False)

    def _request_delivery(self, send):
        if self.demo_mode or self._selected_id is None or self.delivery_config is None or self.delivery_task is not None:
            return
        try:
            preview = delivery.snapshot(self.database_path, self._selected_id, self.delivery_config, self.body_editor.toPlainText())
            if not self._confirmed("Approve sending" if send else "Create Gmail draft", preview, send):
                return
            draft_id, config, provider = self._selected_id, self.delivery_config, self.provider
            self._start_delivery(lambda: delivery.deliver(self.database_path, draft_id, preview["content_hash"], config, provider, send=send))
        except (ValueError, sqlite3.Error) as error:
            self.status_label.setText(str(error))

    def check_delivery(self):
        if self._selected_id and self.delivery_config and not self.demo_mode:
            draft_id, config, provider = self._selected_id, self.delivery_config, self.provider
            self._start_delivery(lambda: delivery.reconcile(self.database_path, draft_id, config, provider))

    def cancel_gmail_draft(self):
        if self._selected_id and self.delivery_config and not self.demo_mode:
            if QMessageBox.question(self, "Cancel Gmail draft", "Delete the staged Gmail draft? The local reply will remain editable.") != QMessageBox.StandardButton.Yes:
                return
            draft_id, config, provider = self._selected_id, self.delivery_config, self.provider
            self._start_delivery(lambda: delivery.cancel_gmail_draft(self.database_path, draft_id, config, provider))

    def refresh(self, preserve_editor=False) -> None:
        """Reload pending drafts and keep the first one selected."""
        previous = self._selected_id
        previous_text = self.body_editor.toPlainText()
        previous_draft = next((draft for draft in self._drafts if draft.id == previous), None)
        dirty = bool(preserve_editor and previous_draft and previous_text != previous_draft.body)
        cursor = self.body_editor.textCursor()
        position, anchor = cursor.position(), cursor.anchor()
        scroll = self.body_editor.verticalScrollBar().value()
        self._drafts = list_pending_drafts(self.database_path)
        self.draft_list.blockSignals(True)
        self.draft_list.clear()
        for draft in self._drafts:
            email = get_email(self.database_path, draft.email_id)
            text = f"{email.from_name + ' <' + email.from_email + '>' if email else 'Unknown sender'}\n{email.subject if email else 'Unknown subject'}"
            self.draft_list.addItem(text)
        self.draft_list.blockSignals(False)
        self.empty_state_label.setVisible(not self._drafts)
        if self._drafts:
            self.select_draft(previous)
        else:
            self._selected_id = None
            self._clear_details()
        if preserve_editor and self._selected_id == previous and previous is not None and not self.body_editor.isReadOnly():
            if dirty:
                self.body_editor.setPlainText(previous_text)
            cursor = self.body_editor.textCursor()
            maximum = self.body_editor.document().characterCount() - 1
            cursor.setPosition(min(anchor, maximum))
            cursor.setPosition(min(position, maximum), cursor.MoveMode.KeepAnchor)
            self.body_editor.setTextCursor(cursor)
            self.body_editor.verticalScrollBar().setValue(scroll)
        self.queue_changed.emit()

    def select_draft(self, draft_id: int | None) -> None:
        row = next((index for index, draft in enumerate(self._drafts) if draft.id == draft_id), 0)
        if self._drafts:
            self.draft_list.setCurrentRow(row)

    def copy_reply(self) -> None:
        if self._selected_id is None:
            return
        QApplication.clipboard().setText(self.body_editor.toPlainText())
        self.status_label.setText("Reply copied. Nothing was sent.")

    def _select_row(self, row: int) -> None:
        if row < 0 or row >= len(self._drafts):
            self._selected_id = None
            self._clear_details()
            return
        draft = self._drafts[row]
        self._selected_id = draft.id
        self.edit_button.setEnabled(True)
        self.reject_button.setEnabled(True)
        self.copy_button.setEnabled(True)
        self.approve_button.setEnabled(True)
        self.approve_button.setEnabled(self.sending_enabled)
        email = get_email(self.database_path, draft.email_id)
        self.recipient_label.setText(email.from_email if email else "Unknown recipient")
        self.subject_label.setText((email.subject if email.subject.lower().startswith("re:") else f"Re: {email.subject}") if email else "Unknown subject")
        self.incoming_label.setPlainText(email.body if email else "Incoming email is unavailable.")
        faq_text = "No saved answer linked."
        if draft.faq_id is not None:
            faq = next((item for item in list_approved_faqs(self.database_path)
                        if item.id == draft.faq_id), None)
            if faq is not None:
                faq_text = f"{faq.question}\n{faq.answer}"
        self.source_faq_label.setPlainText(faq_text)
        if self.review_workflow:
            from app.db.database import open_database
            with open_database(self.database_path) as connection:
                job = connection.execute("SELECT sources_json,reason FROM desktop_draft_jobs WHERE draft_id=?", (draft.id,)).fetchone()
            if job:
                sources = json.loads(job['sources_json'] or '[]')
                self.source_faq_label.setPlainText((job['reason'] or '') + '\n' + '\n\n'.join(item['question'] + '\n' + item['answer'] for item in sources))
        self.body_editor.setPlainText(draft.body)
        self.update_delivery_controls()

    def _clear_details(self) -> None:
        self.edit_button.setEnabled(False)
        self.reject_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        self.approve_button.setEnabled(False)
        self.recipient_label.setText("—")
        self.subject_label.setText("—")
        self.incoming_label.setPlainText("No draft selected.")
        self.source_faq_label.setPlainText("")
        self.body_editor.clear()
        self.update_delivery_controls()

    def _show_error(self, title: str, error: Exception) -> None:
        QMessageBox.warning(self, title, str(error))

    def save_edit(self) -> None:
        if self._selected_id is None:
            return
        try:
            edit_draft(self.database_path, self._selected_id,
                       self.body_editor.toPlainText())
        except (ValueError, sqlite3.Error) as error:
            self._show_error("Could not save edit", error)
            return
        self.refresh()
        self.status_label.setText("Reply changes saved.")

    def reject(self) -> None:
        if self._selected_id is None:
            return
        try:
            reject_draft(self.database_path, self._selected_id)
        except (ValueError, sqlite3.Error) as error:
            self._show_error("Could not reject draft", error)
            return
        self.refresh()

    def approve(self) -> None:
        if not self.sending_enabled:
            return
        self._request_delivery(True)
