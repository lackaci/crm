"""Customer file panel.

The widget deliberately knows only about the public file-storage functions.
SQL and the details of copying files stay in ``app.db.files``.
"""

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.db.files import add_file, list_files


class CustomerFilesWidget(QWidget):
    """Choose, classify, store, and list files belonging to one customer."""

    headers = ("Filename", "Type", "Stored path")
    file_types = ("invoice", "contract", "other")

    def __init__(
        self,
        database_path: Path,
        storage_root: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.database_path = database_path
        self.storage_root = storage_root
        self.selected_customer_id: int | None = None
        self.selected_file_path: Path | None = None

        self.file_path_label = QLabel("No file selected")
        self.file_path_label.setWordWrap(True)
        self.choose_file_button = QPushButton("Choose file")
        self.choose_file_button.clicked.connect(self.choose_file)

        self.classification_combo = QComboBox()
        # Keep these values exactly aligned with the backend's allowed types.
        self.classification_combo.addItems(list(self.file_types))
        self.add_file_button = QPushButton("Store file")
        self.add_file_button.clicked.connect(self.store_selected_file)

        form = QFormLayout()
        file_row = QHBoxLayout()
        file_row.addWidget(self.file_path_label, 1)
        file_row.addWidget(self.choose_file_button)
        form.addRow("Local file", file_row)
        form.addRow("Classification", self.classification_combo)
        form.addRow("", self.add_file_button)

        self.files_table = QTableWidget(0, len(self.headers))
        self.files_table.setHorizontalHeaderLabels(self.headers)
        self.files_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.files_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.files_table.setMinimumHeight(180)
        self.files_table.verticalHeader().hide()

        stored_page = QWidget()
        layout = QVBoxLayout(stored_page)
        layout.addWidget(QLabel("Customer files"))
        layout.addLayout(form)
        layout.addWidget(self.files_table, 1)
        self.empty_label = QLabel("Choose a customer above to see their files.")
        self.empty_label.setWordWrap(True)
        layout.addWidget(self.empty_label)
        from .documents import DocumentsWidget
        self.documents_widget = DocumentsWidget(database_path, storage_root, self)
        self.file_tabs = QTabWidget()
        self.file_tabs.addTab(stored_page, "Stored files")
        self.file_tabs.addTab(self.documents_widget, "Excel register (all customers)")
        self.file_tabs.currentChanged.connect(lambda index: self.documents_widget.refresh() if index == 1 else None)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.file_tabs)

        # Friendly aliases make the small widget convenient to use in tests
        # and from other beginner-level screens.
        self.file_type_combo = self.classification_combo
        self.file_table = self.files_table

    def set_customer(self, customer_id: int | None) -> None:
        """Select a customer and immediately reload that customer's files."""
        self.selected_customer_id = customer_id
        self.selected_file_path = None
        self.file_path_label.setText("No file selected")
        self.refresh()

    def choose_file(self) -> None:
        """Open Qt's native file picker and remember the chosen source path."""
        filename, _filter = QFileDialog.getOpenFileName(
            self, "Choose a file", "", "All files (*)"
        )
        if filename:
            self.selected_file_path = Path(filename)
            self.file_path_label.setText(str(self.selected_file_path))

    def store_selected_file(self) -> None:
        """Store the chosen file through the database/storage API."""
        if self.selected_customer_id is None:
            QMessageBox.warning(
                self, "No customer selected", "Select a customer before storing a file."
            )
            return
        if self.selected_file_path is None:
            QMessageBox.warning(
                self, "No file selected", "Choose a local file before storing it."
            )
            return

        try:
            add_file(
                self.database_path,
                self.selected_customer_id,
                self.selected_file_path,
                self.classification_combo.currentText(),
                self.storage_root,
            )
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Could not store file", str(error))
            return

        self.selected_file_path = None
        self.file_path_label.setText("No file selected")
        self.refresh()
        self.documents_widget.refresh()
        self.documents_widget.process_automatically()

    # A short method name is useful for callers that trigger the action in
    # code, while the button remains labelled with plain user language.
    add_selected_file = store_selected_file

    def refresh(self) -> None:
        """Reload the selected customer's stored-file list."""
        if self.selected_customer_id is None:
            self.files_table.setRowCount(0)
            self.empty_label.setText("Choose a customer above to see their files.")
            self.add_file_button.setEnabled(False)
            return

        files = list_files(self.database_path, self.selected_customer_id)
        self.add_file_button.setEnabled(True)
        self.empty_label.setText("No files yet. Choose a local file to add one." if not files else f"{len(files)} file(s) for this customer.")
        self.files_table.setRowCount(len(files))
        for row, customer_file in enumerate(files):
            self.files_table.setItem(row, 0, QTableWidgetItem(customer_file.filename))
            self.files_table.setItem(row, 1, QTableWidgetItem(customer_file.file_type))
            self.files_table.setItem(row, 2, QTableWidgetItem(customer_file.path))
        self.files_table.resizeColumnsToContents()
