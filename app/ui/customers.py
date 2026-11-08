"""Customer form and table for the Mini CRM window."""

import sqlite3
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QSignalBlocker
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QSplitter,
    QLabel,
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
    customer_changed = Signal(object)
    customers_changed = Signal()

    def __init__(
        self,
        database_path: Path,
        parent: QWidget | None = None,
        email_provider: EmailProvider | None = None,
        llm_provider: LLMProvider | None = None,
        storage_root: Path | None = None,
        gmail_allowed_senders: tuple[str, ...] = (),
    ) -> None:
        super().__init__(parent)
        self.database_path = database_path
        self.selected_customer_id: int | None = None

        self.name_input = QLineEdit()
        self.email_input = QLineEdit()
        self.phone_input = QLineEdit()
        self.notes_input = QTextEdit()
        self.notes_input.setMinimumHeight(90)

        form = QFormLayout()
        form.addRow("Name", self.name_input)
        form.addRow("Email", self.email_input)
        form.addRow("Phone", self.phone_input)
        form.addRow("Notes", self.notes_input)

        self.save_button = QPushButton("Add customer")
        self.clear_button = QPushButton("New customer")
        self.save_button.setObjectName("primaryButton")
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
        self.table.setMinimumHeight(180)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.setColumnHidden(2, True)
        self.table.setColumnHidden(3, True)
        self.table.cellClicked.connect(self.select_customer)
        self.table.itemSelectionChanged.connect(self._select_current_customer)
        self.inbox_widget = CustomerInboxWidget(
            database_path,
            provider=email_provider,
            llm_provider=llm_provider,
            allowed_senders=gmail_allowed_senders,
            parent=self,
            storage_root=storage_root,
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

        details_page = QGroupBox("Customer details")
        details_layout = QVBoxLayout(details_page)
        details_layout.addLayout(form)
        details_layout.addLayout(buttons)
        self.saved_label = QLabel("Choose a customer from the list, or add a new one.")
        self.saved_label.setWordWrap(True)
        details_layout.addWidget(self.saved_label)
        details_layout.addStretch()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Find a customer by name or email")
        self.search_input.setAccessibleName("Find customers")
        self.search_input.textChanged.connect(self._filter_customers)
        list_page = QWidget()
        list_layout = QVBoxLayout(list_page)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.addWidget(self.search_input)
        list_layout.addWidget(self.table, 1)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(list_page)
        splitter.addWidget(details_page)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([550, 350])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)
        self.refresh()

    def refresh(self) -> None:
        """Reload the table from SQLite."""
        customers = list_customers(self.database_path)
        blocker = QSignalBlocker(self.table)
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
            if customer.id == self.selected_customer_id:
                self.table.selectRow(row_index)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        del blocker
        self._filter_customers(self.search_input.text())
        self.customers_changed.emit()

    def _filter_customers(self, text: str) -> None:
        needle = text.strip().casefold()
        for row in range(self.table.rowCount()):
            haystack = " ".join(self.table.item(row, column).text() for column in (0, 1)).casefold()
            self.table.setRowHidden(row, needle not in haystack)

    def select_customer_id(self, customer_id: int) -> None:
        """Let the shared customer selector open the same record as the table."""
        self.search_input.clear()
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == customer_id:
                self.table.selectRow(row)
                self._select_customer_row(row)
                break

    def select_customer(self, row: int, _column: int) -> None:
        """Load the clicked row into the form for editing."""
        self._select_customer_row(row)

    def _select_current_customer(self) -> None:
        """Handle keyboard and whole-row selections as well as mouse clicks."""
        row = self.table.currentRow()
        if row >= 0:
            self._select_customer_row(row)

    def _select_customer_row(self, row: int) -> None:
        """Load the customer represented by one table row."""
        item = self.table.item(row, 0)
        if item is None:
            return
        customer_id = int(item.data(Qt.ItemDataRole.UserRole))
        changed = customer_id != self.selected_customer_id
        self.selected_customer_id = customer_id
        if changed:
            self.inbox_widget.set_customer(customer_id)
            self.files_widget.set_customer(customer_id)
        customer = next(
            customer
            for customer in list_customers(self.database_path)
            if customer.id == self.selected_customer_id
        )
        self._set_form_values(customer)
        self.save_button.setText("Save changes")
        if changed:
            self.customer_changed.emit(customer_id)

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
                customer_id = add_customer(self.database_path, *values)
            else:
                customer_id = self.selected_customer_id
                update_customer(self.database_path, self.selected_customer_id, *values)
        except (ValueError, sqlite3.IntegrityError) as error:
            QMessageBox.warning(self, "Could not save customer", str(error))
            return
        self.refresh()
        self.select_customer_id(customer_id)
        self.saved_label.setText("Customer saved. Their inbox and files are available in the menu.")

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
        blocker = QSignalBlocker(self.table)
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        del blocker
        self.saved_label.setText("Enter the new customer's details, then choose Add customer.")
        self.customer_changed.emit(None)

    def _set_form_values(self, customer: Customer) -> None:
        self.name_input.setText(customer.name)
        self.email_input.setText(customer.email)
        self.phone_input.setText(customer.phone)
        self.notes_input.setPlainText(customer.notes)
