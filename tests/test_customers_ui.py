"""Tests for the customer widget."""

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from app.db.customers import list_customers
from app.db.customers import add_customer
from app.db.database import initialize_database
from app.providers.email_mock import MockEmailProvider
from app.ui.customers import CustomersWidget


@pytest.fixture
def qapp():
    application = QApplication.instance() or QApplication([])
    yield application


def test_widget_adds_and_edits_customer(qapp, tmp_path: Path):
    database_path = tmp_path / "ui-test.db"
    initialize_database(database_path)
    widget = CustomersWidget(database_path)

    widget.name_input.setText("Grace Hopper")
    widget.email_input.setText("grace@example.com")
    widget.save_customer()
    assert [customer.name for customer in list_customers(database_path)] == [
        "Grace Hopper"
    ]

    widget.table.cellClicked.emit(0, 0)
    widget.name_input.setText("Grace Brewster Hopper")
    widget.save_customer()

    assert list_customers(database_path)[0].name == "Grace Brewster Hopper"


def test_widget_imports_and_displays_selected_customer_email(qapp, tmp_path: Path):
    """The customer selection is connected to the inbox display."""
    database_path = tmp_path / "inbox-ui-test.db"
    initialize_database(database_path)
    customer_id = add_customer(database_path, "Anna Kovacs", "anna@example.com")

    class FixedProvider(MockEmailProvider):
        def fetch_new_emails(self) -> list[dict[str, object]]:
            return [
                {
                    "id": "fixture-1",
                    "from_name": "Anna Kovacs",
                    "from_email": "anna@example.com",
                    "subject": "A question",
                    "body": "Could you call me back?",
                }
            ]

    widget = CustomersWidget(database_path, email_provider=FixedProvider())
    # Selecting by row is the same Qt signal used by a real mouse click.
    widget.table.cellClicked.emit(0, 0)
    widget.inbox_widget.import_sample_emails()

    assert widget.email_table.rowCount() == 1
    assert widget.email_table.item(0, 0).text() == "A question"
    assert "anna@example.com" in widget.email_table.item(0, 1).text()
    assert widget.email_table.item(0, 2).text() == "Could you call me back?"
