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
    QMessageBox,
    QPushButton,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.db.emails import (
    EmailRecord,
    import_emails,
    list_emails_for_customer,
    mark_email_seen,
)
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
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Inbox", parent)
        self.database_path = database_path
        # Dependency injection makes this panel usable with a deterministic
        # provider in tests, while the real app uses the JSON mock provider.
        self.provider = provider or MockEmailProvider()
        self.llm_provider = llm_provider or FakeLLMProvider()
        self.customer_id: int | None = None
        self.selected_email_id: int | None = None
        self._messages: list[EmailRecord] = []

        self.import_button = QPushButton("Import sample emails")
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
        self.email_table.setWordWrap(True)
        self.email_table.setAlternatingRowColors(True)

        self.import_button.clicked.connect(self.import_sample_emails)
        self.load_faqs_button.clicked.connect(self.load_sample_faqs)
        self.create_draft_button.clicked.connect(self.create_draft)
        self.email_table.itemSelectionChanged.connect(self._select_email)
        layout = QVBoxLayout(self)
        layout.addWidget(self.import_button)
        layout.addWidget(self.load_faqs_button)
        layout.addWidget(self.create_draft_button)
        layout.addWidget(self.status_label)
        layout.addWidget(self.email_table)

    def set_customer(self, customer_id: int | None) -> None:
        """Show the messages for ``customer_id`` and refresh immediately."""
        self.customer_id = customer_id
        self.selected_email_id = None
        self.refresh()

    def refresh(self) -> None:
        """Reload this customer's messages through the database helper."""
        messages = (
            list_emails_for_customer(self.database_path, self.customer_id)
            if self.customer_id is not None
            else []
        )
        self._messages = messages
        self._show_messages(messages)

    def import_sample_emails(self) -> None:
        """Import from the mock provider and refresh the selected customer."""
        try:
            imported = import_emails(self.database_path, self.provider)
        except (
            OSError,
            json.JSONDecodeError,
            ValueError,
            sqlite3.Error,
        ) as error:
            # These are expected failures while reading provider data or
            # storing it.  Unexpected programming errors should still raise.
            QMessageBox.warning(self, "Could not import emails", str(error))
            return

        self.refresh()
        self.status_label.setText(f"{imported} new email(s) imported.")

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
            self.email_table.resizeRowToContents(row)
        self.email_table.resizeColumnsToContents()
        # Keep the message column useful when the window is resized.
        self.email_table.setColumnWidth(2, max(260, self.email_table.columnWidth(2)))

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
