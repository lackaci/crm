"""The small inbox panel used by the customer screen.

The widget deliberately knows nothing about SQL.  It asks the database and
provider layers to do the work, which keeps the Qt code easy to read.
"""

import json
import sqlite3
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QPlainTextEdit,
    QComboBox,
    QVBoxLayout,
    QWidget,
)

from app.db.emails import (
    EmailRecord,
    import_emails,
    list_emails_for_customer,
    list_unmatched_emails,
    mark_email_seen,
    remove_gmail_emails_not_from,
    assign_email_to_customer,
)
from app.db.customers import list_customers
from app.email_sync import synchronize_email
from app.db.faqs import create_faq, list_approved_faqs
from app.providers.llm_base import LLMProvider
from app.providers.llm_fake import FakeLLMProvider
from app.reply.pipeline import process_email
from app.providers.email_base import EmailProvider
from app.providers.email_mock import MockEmailProvider


class CustomerInboxWidget(QGroupBox):
    """Import and display messages belonging to one selected customer."""

    headers = ("Subject", "From", "Message", "Status")
    status_changed = Signal()

    def __init__(
        self,
        database_path: Path,
        provider: EmailProvider | None = None,
        llm_provider: LLMProvider | None = None,
        allowed_senders: tuple[str, ...] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Inbox", parent)
        self.database_path = database_path
        # Dependency injection makes this panel usable with a deterministic
        # provider in tests, while the real app uses the JSON mock provider.
        self.provider = provider or MockEmailProvider()
        self.llm_provider = llm_provider or FakeLLMProvider()
        self.allowed_senders = allowed_senders
        self.customer_id: int | None = None
        self.selected_email_id: int | None = None
        self._messages: list[EmailRecord] = []

        self.import_button = QPushButton("Import emails")
        self.show_unmatched_button = QPushButton("Refresh unmatched emails")
        self.cleanup_button = QPushButton("Clean up other Gmail")
        self.assign_button = QPushButton("Assign unmatched email")
        self.assign_button.setEnabled(False)
        self.customer_combo = QComboBox()
        self.customer_combo.setMinimumWidth(180)
        self.load_faqs_button = QPushButton("Load approved sample FAQs")
        self.create_draft_button = QPushButton("Create safe draft for selected email")
        self.create_draft_button.setEnabled(False)
        self.status_label = QLabel("Select a customer to read their emails.")
        self.email_table = QTableWidget(0, len(self.headers))
        self.email_table.setHorizontalHeaderLabels(self.headers)
        self.email_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.email_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.email_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.email_table.setWordWrap(False)
        self.email_table.setAlternatingRowColors(True)
        self.email_table.setMinimumHeight(150)
        self.email_table.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.unmatched_table = QTableWidget(0, len(self.headers))
        self.unmatched_table.setHorizontalHeaderLabels(self.headers)
        self.unmatched_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.unmatched_table.setWordWrap(False)
        self.unmatched_table.setAlternatingRowColors(True)
        self.unmatched_table.setMinimumHeight(150)
        self.unmatched_table.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.inbox_tabs = QTabWidget()
        self.inbox_tabs.addTab(self.email_table, "Customer emails")
        self.inbox_tabs.addTab(self.unmatched_table, "Unmatched emails")
        self.email_table.setColumnHidden(2, True)
        self.unmatched_table.setColumnHidden(2, True)
        self.email_table.setColumnWidth(0, 330)
        self.email_table.setColumnWidth(1, 260)
        self.email_table.setColumnWidth(3, 80)
        self.unmatched_table.setColumnWidth(0, 330)
        self.unmatched_table.setColumnWidth(1, 260)
        self.unmatched_table.setColumnWidth(3, 80)
        self.email_preview = QPlainTextEdit()
        self.email_preview.setReadOnly(True)
        self.email_preview.setPlaceholderText("Select an email to read it.")
        self.email_preview.setMinimumHeight(260)
        self.email_preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.inbox_tabs.setMinimumHeight(430)
        self.setMinimumHeight(500)

        self.import_button.clicked.connect(self.import_sample_emails)
        self.show_unmatched_button.clicked.connect(self.refresh_unmatched)
        self.cleanup_button.clicked.connect(self.cleanup_other_gmail)
        self.load_faqs_button.clicked.connect(self.load_sample_faqs)
        self.create_draft_button.clicked.connect(self.create_draft)
        self.email_table.itemSelectionChanged.connect(self._select_email)
        self.unmatched_table.itemSelectionChanged.connect(self._select_unmatched_email)
        self.assign_button.clicked.connect(self.assign_selected_email)
        layout = QVBoxLayout(self)
        actions = QHBoxLayout()
        actions.addWidget(self.import_button)
        actions.addWidget(self.show_unmatched_button)
        actions.addWidget(self.cleanup_button)
        actions.addWidget(self.customer_combo)
        actions.addWidget(self.assign_button)
        actions.addWidget(self.load_faqs_button)
        actions.addWidget(self.create_draft_button)
        layout.addLayout(actions)
        layout.addWidget(self.status_label)
        layout.addWidget(self.inbox_tabs, 1)
        layout.addWidget(self.email_preview, 2)
        self.refresh_unmatched()

    def cleanup_other_gmail(self) -> None:
        """Remove previously imported Gmail messages outside the allowlist."""
        if not self.allowed_senders:
            QMessageBox.warning(
                self, "No sender allowlist", "Add allowed Gmail senders in .env first."
            )
            return
        answer = QMessageBox.question(
            self,
            "Clean up other Gmail",
            "Remove previously imported Gmail messages from other senders?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed = remove_gmail_emails_not_from(
            self.database_path, self.allowed_senders
        )
        self.refresh()
        self.refresh_unmatched()
        self.status_label.setText(f"{removed} non-allowed Gmail email(s) removed.")

    def set_customer(self, customer_id: int | None) -> None:
        """Show the messages for ``customer_id`` and refresh immediately."""
        self.customer_id = customer_id
        self.selected_email_id = None
        self.inbox_tabs.setCurrentWidget(self.email_table)
        self.email_preview.clear()
        self.refresh()
        if customer_id is None:
            self.status_label.setText("Select a customer to read their emails.")
        else:
            self.status_label.setText(
                f"{len(self._messages)} email(s) assigned to this customer."
            )

    def refresh(self) -> None:
        """Reload this customer's messages through the database helper."""
        messages = (
            list_emails_for_customer(self.database_path, self.customer_id)
            if self.customer_id is not None
            else []
        )
        self._messages = messages
        self._show_messages(messages)

    def refresh_unmatched(self) -> None:
        """Show imported messages that need customer assignment."""
        selected = self.customer_combo.currentData()
        self.customer_combo.clear()
        for customer in list_customers(self.database_path):
            self.customer_combo.addItem(
                f"{customer.name} <{customer.email}>", customer.id
            )
        if selected is not None:
            index = self.customer_combo.findData(selected)
            if index >= 0:
                self.customer_combo.setCurrentIndex(index)
        self._show_unmatched_messages(list_unmatched_emails(self.database_path))

    def import_sample_emails(self) -> None:
        """Import from the configured provider and refresh both inbox views."""
        result = synchronize_email(self.database_path, self.provider)
        if result.error:
            QMessageBox.warning(self, "Could not import emails", result.error)
            return

        self.refresh()
        self.refresh_unmatched()
        self.status_label.setText(f"{result.imported} new email(s) imported.")

    def _show_messages(self, messages: list[EmailRecord]) -> None:
        self.email_table.setRowCount(len(messages))
        self.selected_email_id = None
        self.create_draft_button.setEnabled(False)
        for row, message in enumerate(messages):
            subject_item = QTableWidgetItem(message.subject)
            subject_item.setData(Qt.ItemDataRole.UserRole, message.id)
            self.email_table.setItem(row, 0, subject_item)
            self.email_table.setItem(
                row, 1, QTableWidgetItem(f"{message.from_name} <{message.from_email}>")
            )
            self.email_table.setItem(row, 2, QTableWidgetItem(message.body))
            self.email_table.setItem(row, 3, QTableWidgetItem(message.status.title()))
            self.email_table.setRowHeight(row, 30)
        self.email_table.resizeColumnsToContents()
        # Keep the message column useful when the window is resized.
        self.email_table.setColumnWidth(2, max(260, self.email_table.columnWidth(2)))
        self.email_table.setColumnWidth(0, 330)
        self.email_table.setColumnWidth(1, 260)
        self.email_table.setColumnWidth(3, 80)

    def _select_unmatched_email(self) -> None:
        """Show an unmatched message in the shared preview."""
        items = self.unmatched_table.selectedItems()
        if not items:
            self.assign_button.setEnabled(False)
            return
        self.assign_button.setEnabled(True)
        email_id = int(
            self.unmatched_table.item(items[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        )
        message = next(
            (item for item in list_unmatched_emails(self.database_path) if item.id == email_id),
            None,
        )
        if message is not None:
            self.email_preview.setPlainText(
                f"From: {message.from_name} <{message.from_email}>\n"
                f"Subject: {message.subject}\n\n{message.body}"
            )

    def assign_selected_email(self) -> None:
        """Assign the selected unmatched message to the chosen customer."""
        items = self.unmatched_table.selectedItems()
        customer_id = self.customer_combo.currentData()
        if not items or customer_id is None:
            return
        email_id = int(
            self.unmatched_table.item(items[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        )
        try:
            assign_email_to_customer(self.database_path, email_id, int(customer_id))
        except (ValueError, sqlite3.Error) as error:
            QMessageBox.warning(self, "Could not assign email", str(error))
            return
        self.refresh()
        self.refresh_unmatched()
        self.status_label.setText("Email assigned to the selected customer.")

    def _show_unmatched_messages(self, messages: list[EmailRecord]) -> None:
        """Render messages whose sender is not yet a CRM customer."""
        self.unmatched_table.setRowCount(len(messages))
        for row, message in enumerate(messages):
            subject_item = QTableWidgetItem(message.subject)
            subject_item.setData(Qt.ItemDataRole.UserRole, message.id)
            self.unmatched_table.setItem(row, 0, subject_item)
            self.unmatched_table.setItem(
                row, 1, QTableWidgetItem(f"{message.from_name} <{message.from_email}>")
            )
            self.unmatched_table.setItem(row, 2, QTableWidgetItem(message.body))
            self.unmatched_table.setItem(
                row, 3, QTableWidgetItem(message.status.title())
            )
            self.unmatched_table.setRowHeight(row, 30)
        self.unmatched_table.resizeColumnsToContents()
        self.unmatched_table.setColumnWidth(
            2, max(260, self.unmatched_table.columnWidth(2))
        )
        self.unmatched_table.setColumnWidth(0, 330)
        self.unmatched_table.setColumnWidth(1, 260)
        self.unmatched_table.setColumnWidth(3, 80)

    def _select_email(self) -> None:
        items = self.email_table.selectedItems()
        if not items:
            self.selected_email_id = None
            self.create_draft_button.setEnabled(False)
            return
        self.selected_email_id = int(
            self.email_table.item(items[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        )
        mark_email_seen(self.database_path, self.selected_email_id)
        self.status_changed.emit()
        self._messages = [
            message
            if message.id != self.selected_email_id
            else EmailRecord(
                message.id,
                message.customer_id,
                message.external_id,
                message.from_name,
                message.from_email,
                message.subject,
                message.body,
                message.received_at,
                "seen",
            )
            for message in self._messages
        ]
        self.email_table.setItem(items[0].row(), 3, QTableWidgetItem("Seen"))
        message = next(
            (item for item in self._messages if item.id == self.selected_email_id),
            None,
        )
        if message is not None:
            self.email_preview.setPlainText(
                f"From: {message.from_name} <{message.from_email}>\n"
                f"Subject: {message.subject}\n\n{message.body}"
            )
        self.create_draft_button.setEnabled(True)

    def create_draft(self) -> None:
        """Create a pending draft or explain why the message was escalated."""
        if self.selected_email_id is None:
            return
        message = next(
            (
                item
                for item in self._messages
                if item.id == self.selected_email_id
            ),
            None,
        )
        if message is None:
            return
        try:
            result = process_email(message, self.database_path, self.llm_provider)
        except (OSError, ValueError, sqlite3.Error, RuntimeError) as error:
            QMessageBox.warning(self, "Could not create draft", str(error))
            return
        if result.status == "pending":
            self.status_label.setText("Draft created. Open Approval Queue to review it.")
        else:
            QMessageBox.information(
                self,
                "Message escalated",
                result.reason or "This message needs owner review.",
            )

    def load_sample_faqs(self) -> None:
        """Load the local fixture as owner-approved FAQs for this prototype."""
        faq_path = Path(__file__).resolve().parents[2] / "sample_data" / "faqs.json"
        try:
            records = json.loads(faq_path.read_text(encoding="utf-8"))
            if not isinstance(records, list):
                raise ValueError("Sample FAQ data must be a JSON list.")
            existing_questions = {
                faq.question.casefold() for faq in list_approved_faqs(self.database_path)
            }
            loaded = 0
            for record in records:
                if not isinstance(record, dict):
                    raise ValueError("Sample FAQ records must be mappings.")
                question = record.get("question")
                answer = record.get("answer")
                if not isinstance(question, str) or not isinstance(answer, str):
                    raise ValueError("Each sample FAQ needs a question and answer.")
                if question.casefold() in existing_questions:
                    continue
                create_faq(
                    self.database_path,
                    question,
                    answer,
                    approved=True,
                    actor="sample-faq-loader",
                )
                existing_questions.add(question.casefold())
                loaded += 1
        except (OSError, json.JSONDecodeError, ValueError, sqlite3.Error) as error:
            QMessageBox.warning(self, "Could not load sample FAQs", str(error))
            return
        self.status_label.setText(f"{loaded} approved FAQ(s) loaded.")
