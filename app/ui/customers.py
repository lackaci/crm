"""Customer form and table for the Mini CRM window."""

import sqlite3
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.db.customers import Customer, add_customer, list_customers, update_customer
from app.db.emails import customer_email_status
from app.providers.email_base import EmailProvider
from app.providers.llm_base import LLMProvider
from app.ui.inbox import CustomerInboxWidget
from app.ui.customer_files import CustomerFilesWidget


class CustomersWidget(QWidget):
    """Let the owner add, edit, and browse customers."""

    headers = ("Name", "Email", "Phone", "Notes", "Status")

    def __init__(
        self,
        database_path: Path,
        parent: QWidget | None = None,
        email_provider: EmailProvider | None = None,
        llm_provider: LLMProvider | None = None,
        storage_root: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.database_path = database_path
        self.selected_customer_id: int | None = None

        self.name_input = QLineEdit()
        self.email_input = QLineEdit()
        self.phone_input = QLineEdit()
        self.notes_input = QTextEdit()
        self.notes_input.setFixedHeight(70)

        form = QFormLayout()
        form.addRow("Name", self.name_input)
        form.addRow("Email", self.email_input)
        form.addRow("Phone", self.phone_input)
        form.addRow("Notes", self.notes_input)

        self.save_button = QPushButton("Add customer")
        self.clear_button = QPushButton("Clear")
        self.save_button.clicked.connect(self.save_customer)
        self.clear_button.clicked.connect(self.clear_form)

        buttons = QHBoxLayout()
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.clear_button)
        buttons.addStretch()

        self.table = QTableWidget(0, len(self.headers))
        self.table.setHorizontalHeaderLabels(self.headers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.cellClicked.connect(self.select_customer)
        self.inbox_widget = CustomerInboxWidget(
            database_path,
            provider=email_provider,
            llm_provider=llm_provider,
            parent=self,
        )
        self.inbox_widget.status_changed.connect(self.refresh)
        # Convenient aliases keep the connected inbox easy to discover for
        # callers without changing the customer form's existing attributes.
        self.import_emails_button = self.inbox_widget.import_button
        self.email_table = self.inbox_widget.email_table
        self.create_draft_button = self.inbox_widget.create_draft_button
        self.files_widget = CustomerFilesWidget(
            database_path, storage_root=storage_root, parent=self
        )
        # This alias keeps the panel easy to find without changing the
        # existing inbox/customer public attributes.
        self.customer_files_widget = self.files_widget

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.table)
        layout.addWidget(self.inbox_widget)
        layout.addWidget(self.files_widget)
        self.refresh()

    def refresh(self) -> None:
        """Reload the table from SQLite."""
        customers = list_customers(self.database_path)
        self.table.setRowCount(len(customers))
        for row_index, customer in enumerate(customers):
            self.table.setItem(row_index, 0, QTableWidgetItem(customer.name))
            self.table.setItem(row_index, 1, QTableWidgetItem(customer.email))
            self.table.setItem(row_index, 2, QTableWidgetItem(customer.phone))
            self.table.setItem(row_index, 3, QTableWidgetItem(customer.notes))
            self.table.setItem(
                row_index,
                4,
                QTableWidgetItem(customer_email_status(self.database_path, customer.id)),
            )
            self.table.item(row_index, 0).setData(Qt.ItemDataRole.UserRole, customer.id)
        self.table.resizeColumnsToContents()

    def select_customer(self, row: int, _column: int) -> None:
        """Load the clicked row into the form for editing."""
        customer_id = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        self.selected_customer_id = int(customer_id)
        self.inbox_widget.set_customer(self.selected_customer_id)
        self.files_widget.set_customer(self.selected_customer_id)
        customer = next(
            customer
            for customer in list_customers(self.database_path)
            if customer.id == self.selected_customer_id
        )
        self._set_form_values(customer)
        self.save_button.setText("Save changes")

    def save_customer(self) -> None:
        """Add a new customer or save the selected customer."""
        try:
            values = (
                self.name_input.text(),
                self.email_input.text(),
                self.phone_input.text(),
                self.notes_input.toPlainText(),
            )
            if self.selected_customer_id is None:
                add_customer(self.database_path, *values)
            else:
                update_customer(self.database_path, self.selected_customer_id, *values)
        except (ValueError, sqlite3.IntegrityError) as error:
            QMessageBox.warning(self, "Could not save customer", str(error))
            return
        self.clear_form()
        self.refresh()

    def clear_form(self) -> None:
        """Reset the form so the next save creates a customer."""
        self.selected_customer_id = None
        self.inbox_widget.set_customer(None)
        self.files_widget.set_customer(None)
        self.name_input.clear()
        self.email_input.clear()
        self.phone_input.clear()
        self.notes_input.clear()
        self.save_button.setText("Add customer")
        self.table.clearSelection()

    def _set_form_values(self, customer: Customer) -> None:
        self.name_input.setText(customer.name)
        self.email_input.setText(customer.email)
        self.phone_input.setText(customer.phone)
        self.notes_input.setPlainText(customer.notes)
