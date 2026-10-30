"""A beginner-friendly review screen for generated reply drafts."""

import sqlite3
from pathlib import Path

from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.db.emails import get_email
from app.db.faqs import list_approved_faqs
from app.providers.email_base import EmailDeliveryError, EmailProvider
from app.providers.email_mock import MockEmailProvider
from app.reply.approval import (
    approve_draft,
    edit_draft,
    list_pending_drafts,
    reject_draft,
)


class ApprovalQueueWidget(QWidget):
    """Display pending drafts and let a reviewer edit, reject, or approve them.

    The widget deliberately calls application/database helpers instead of
    containing SQL.  The provider is injected so tests can use a safe fake.
    """

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
        self._drafts = []
        self._selected_id: int | None = None

        self.draft_list = QListWidget()
        self.draft_list.setAccessibleName("Pending drafts")
        self.draft_list.currentRowChanged.connect(self._select_row)
        # Keep the empty state outside the list so it remains visible when
        # QListWidget has no rows.
        self.empty_state_label = QLabel("No pending drafts.")
        self.empty_state_label.setObjectName("emptyStateLabel")

        self.recipient_label = QLabel("—")
        self.subject_label = QLabel("—")
        self.incoming_label = QLabel("No draft selected.")
        self.incoming_label.setWordWrap(True)
        self.source_faq_label = QLabel("—")
        self.source_faq_label.setWordWrap(True)
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
        details.addRow("Source FAQ", self.source_faq_label)
        details.addRow("Proposed reply", self.body_editor)

        self.edit_button = QPushButton("Save edit")
        self.reject_button = QPushButton("Reject")
        self.approve_button = QPushButton("Approve and send")
        self.edit_button.clicked.connect(self.save_edit)
        self.reject_button.clicked.connect(self.reject)
        self.approve_button.clicked.connect(self.approve)
        self.approve_button.setEnabled(False)
        self.approve_button.setToolTip("Sending is disabled until M12.")

        actions = QHBoxLayout()
        actions.addWidget(self.edit_button)
        actions.addWidget(self.reject_button)
        actions.addWidget(self.approve_button)
        actions.addStretch()

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Pending replies"))
        layout.addWidget(self.empty_state_label)
        layout.addWidget(self.draft_list)
        layout.addLayout(details)
        layout.addLayout(actions)
        self.refresh()

    def refresh(self) -> None:
        """Reload pending drafts and keep the first one selected."""
        self._drafts = list_pending_drafts(self.database_path)
        self.draft_list.blockSignals(True)
        self.draft_list.clear()
        for draft in self._drafts:
            email = get_email(self.database_path, draft.email_id)
            text = f"{email.from_email if email else 'Unknown sender'} — {email.subject if email else 'Unknown subject'}"
            self.draft_list.addItem(text)
        self.draft_list.blockSignals(False)
        self.empty_state_label.setVisible(not self._drafts)
        if self._drafts:
            self.draft_list.setCurrentRow(0)
        else:
            self._selected_id = None
            self._clear_details()

    def _select_row(self, row: int) -> None:
        if row < 0 or row >= len(self._drafts):
            self._selected_id = None
            self._clear_details()
            return
        draft = self._drafts[row]
        self._selected_id = draft.id
        self.edit_button.setEnabled(True)
        self.reject_button.setEnabled(True)
        self.approve_button.setEnabled(True)
        self.approve_button.setEnabled(self.sending_enabled)
        email = get_email(self.database_path, draft.email_id)
        self.recipient_label.setText(email.from_email if email else "Unknown recipient")
        self.subject_label.setText(f"Re: {email.subject}" if email else "Unknown subject")
        self.incoming_label.setText(email.body if email else "Incoming email is unavailable.")
        faq_text = "No FAQ linked."
        if draft.faq_id is not None:
            faq = next((item for item in list_approved_faqs(self.database_path)
                        if item.id == draft.faq_id), None)
            if faq is not None:
                faq_text = f"{faq.question}\n{faq.answer}"
        self.source_faq_label.setText(faq_text)
        self.body_editor.setPlainText(draft.body)

    def _clear_details(self) -> None:
        self.edit_button.setEnabled(False)
        self.reject_button.setEnabled(False)
        self.approve_button.setEnabled(False)
        self.recipient_label.setText("—")
        self.subject_label.setText("—")
        self.incoming_label.setText("No draft selected.")
        self.source_faq_label.setText("—")
        self.body_editor.clear()

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
        if self._selected_id is None:
            return
        try:
            approve_draft(
                self.database_path,
                self._selected_id,
                self.provider,
                sending_enabled=self.sending_enabled,
            )
        except (EmailDeliveryError, ValueError, sqlite3.Error) as error:
            self._show_error("Could not approve draft", error)
            return
        self.refresh()
