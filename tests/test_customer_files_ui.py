"""Focused Qt tests for the customer file panel."""

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from app.db.customers import add_customer
from app.db.database import initialize_database
from app.ui.customer_files import CustomerFilesWidget


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def test_file_is_classified_stored_and_listed(
    qapp, tmp_path: Path, monkeypatch
):
    database_path = tmp_path / "files.db"
    initialize_database(database_path)
    customer_id = add_customer(database_path, "Ada Lovelace", "ada@example.com")
    source = tmp_path / "agreement.txt"
    source.write_text("terms", encoding="utf-8")

    monkeypatch.setattr(
        "app.ui.customer_files.QFileDialog.getOpenFileName",
        lambda *args: (str(source), "All files (*)"),
    )
    widget = CustomerFilesWidget(database_path, tmp_path / "stored")
    widget.set_customer(customer_id)
    widget.choose_file()
    widget.classification_combo.setCurrentText("contract")
    widget.store_selected_file()

    assert widget.files_table.rowCount() == 1
    assert widget.files_table.item(0, 0).text() == "agreement.txt"
    assert widget.files_table.item(0, 1).text() == "contract"
    assert widget.files_table.item(0, 2).text() == "customer_1/agreement.txt"
    assert (tmp_path / "stored" / "customer_1" / "agreement.txt").read_text(
        encoding="utf-8"
    ) == "terms"


def test_file_panel_reports_missing_selection_and_duplicate(
    qapp, tmp_path: Path, monkeypatch
):
    database_path = tmp_path / "files.db"
    initialize_database(database_path)
    customer_id = add_customer(database_path, "Ada Lovelace", "ada@example.com")
    source = tmp_path / "invoice.pdf"
    source.write_bytes(b"invoice")
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, message: messages.append((title, message)),
    )
    widget = CustomerFilesWidget(database_path, tmp_path / "stored")
    widget.set_customer(customer_id)
    widget.store_selected_file()
    assert "Choose a local file" in messages[-1][1]

    widget.selected_file_path = source
    widget.store_selected_file()
    widget.selected_file_path = source
    widget.store_selected_file()
    assert "already exists" in messages[-1][1]
