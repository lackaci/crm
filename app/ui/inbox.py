"""The small inbox panel used by the customer screen.

The widget deliberately knows nothing about SQL.  It asks the database and
provider layers to do the work, which keeps the Qt code easy to read.
"""

import json
import sqlite3
from pathlib import Path

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

from app.db.emails import EmailRecord, import_emails, list_emails_for_customer
from app.providers.email_base import EmailProvider
from app.providers.email_mock import MockEmailProvider


class CustomerInboxWidget(QGroupBox):
    """Import and display messages belonging to one selected customer."""

    headers = ("Subject", "From", "Message")

    def __init__(
        self,
        database_path: Path,
        provider: EmailProvider | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Inbox", parent)
        self.database_path = database_path
        # Dependency injection makes this panel usable with a deterministic
        # provider in tests, while the real app uses the JSON mock provider.
        self.provider = provider or MockEmailProvider()
        self.customer_id: int | None = None

        self.import_button = QPushButton("Import sample emails")
        self.status_label = QLabel("Select a customer to read their emails.")
        self.email_table = QTableWidget(0, len(self.headers))
        self.email_table.setHorizontalHeaderLabels(self.headers)
        # These settings make the table a readable display rather than an
        # editor.  They are Qt presentation choices, not data-layer logic.
        self.email_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self.email_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.email_table.setWordWrap(True)
        self.email_table.setAlternatingRowColors(True)

        self.import_button.clicked.connect(self.import_sample_emails)
        layout = QVBoxLayout(self)
        layout.addWidget(self.import_button)
        layout.addWidget(self.status_label)
        layout.addWidget(self.email_table)

    def set_customer(self, customer_id: int | None) -> None:
        """Show the messages for ``customer_id`` and refresh immediately."""
        self.customer_id = customer_id
        self.refresh()

    def refresh(self) -> None:
        """Reload this customer's messages through the database helper."""
        messages = (
            list_emails_for_customer(self.database_path, self.customer_id)
            if self.customer_id is not None
            else []
        )
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
        for row, message in enumerate(messages):
            self.email_table.setItem(row, 0, QTableWidgetItem(message.subject))
            self.email_table.setItem(
                row, 1, QTableWidgetItem(f"{message.from_name} <{message.from_email}>")
            )
            self.email_table.setItem(row, 2, QTableWidgetItem(message.body))
            self.email_table.resizeRowToContents(row)
        self.email_table.resizeColumnsToContents()
        # Keep the message column useful when the window is resized.
        self.email_table.setColumnWidth(2, max(260, self.email_table.columnWidth(2)))
