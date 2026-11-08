"""Document processing and a shared Excel register inside Files."""
from copy import deepcopy
import json
from pathlib import Path

from PySide6.QtCore import QThread, Signal, Qt, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog,
    QHeaderView, QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QScrollArea, QToolButton,
    QTabWidget, QVBoxLayout, QWidget)

from app.db import documents as store
from app.db.files import DEFAULT_STORAGE_ROOT
from app.documents import process_documents, review_extraction, normalize_labeled_facts
from app.document_workbook import export_workbook
from app.providers.document_model import DocumentError, FIELD_NAMES
from app.providers.document_factory import document_provider
from app.providers.online_model import OnlineSettings, OnlineClient, DEFAULT_ONLINE_MODEL, OPENAI_ENDPOINT, save_api_key
from app.providers.online_model import VERCEL_ENDPOINT, VERCEL_MODEL, VERCEL_EMBEDDING_MODEL, VERCEL_KEY_ENV


class DocumentTask(QThread):
    progress = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            value = self.operation(self)
        except (DocumentError, ValueError) as error:
            self.failed.emit(str(error))
        except Exception:
            self.failed.emit("Document processing could not finish. Check the selected AI provider and workbook, then retry.")
        else:
            self.succeeded.emit(value)


class ExtractionReviewDialog(QDialog):
    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.source = source
        self.data = normalize_labeled_facts(deepcopy(json.loads(source["data_json"])))
        self.approve = False
        self.setWindowTitle("Review extracted document data")
        self.resize(900, 600)
        layout = QVBoxLayout(self)
        note = QLabel("Check values against the original file. You can correct values below. Evidence is the model's quotation, not independent verification.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.type_combo = QComboBox()
        self.type_combo.addItems(["invoice", "receipt", "contract", "other"])
        self.type_combo.setCurrentText(self.data["document_type"])
        type_form = QFormLayout()
        type_form.addRow("Document type", self.type_combo)
        layout.addLayout(type_form)
        tabs = self.tabs = QTabWidget()
        self.tables = {}
        self.keys = {"fields": ("name", "value", "page", "evidence"), "facts": ("label", "value", "page", "evidence"),
            "line_items": ("description", "quantity", "unit_price", "tax_rate", "total", "page", "evidence"),
            "summaries": ("page", "title", "summary")}
        for name, keys in self.keys.items():
            rows = self.data[name]
            table = QTableWidget(len(rows), len(keys))
            table.setHorizontalHeaderLabels([key.replace("_", " ").title() for key in keys])
            for index, row in enumerate(rows):
                for column, key in enumerate(keys):
                    item = QTableWidgetItem("" if row[key] is None else str(row[key]))
                    if key in {"name", "page", "evidence"}:
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    table.setItem(index, column, item)
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            self.tables[name] = table
            tabs.addTab(table, name.replace("_", " ").title())
        layout.addWidget(tabs, 1)
        rows = QHBoxLayout()
        add_row = QPushButton("Add missing entry")
        remove_row = QPushButton("Remove selected entry")
        add_row.clicked.connect(self.add_row)
        remove_row.clicked.connect(self.remove_row)
        rows.addWidget(add_row)
        rows.addWidget(remove_row)
        rows.addStretch()
        layout.addLayout(rows)
        buttons = QHBoxLayout()
        save = QPushButton("Save corrections")
        reviewed = QPushButton("Mark reviewed")
        cancel = QPushButton("Cancel")
        save.clicked.connect(lambda: self.finish(False))
        reviewed.clicked.connect(lambda: self.finish(True))
        cancel.clicked.connect(self.reject)
        for button in (save, reviewed, cancel):
            buttons.addWidget(button)
        layout.addLayout(buttons)

    def finish(self, approve):
        self.data["document_type"] = self.type_combo.currentText()
        for name, table in self.tables.items():
            rows = []
            for index in range(table.rowCount()):
                row = {}
                for column, key in enumerate(self.keys[name]):
                    text = table.item(index, column).text()
                    row[key] = int(text) if key == "page" else text or (None if name == "line_items" and key not in {"description", "evidence"} else "")
                rows.append(row)
            self.data[name] = rows
        self.approve = approve
        self.accept()

    def add_row(self):
        name = list(self.tables)[self.tabs.currentIndex()]
        if name == "summaries":
            return  # Source sections cannot be invented; their text is editable.
        field = ""
        if name == "fields":
            field, accepted = QInputDialog.getItem(self, "Add missing field", "Field", list(FIELD_NAMES), 0, False)
            if not accepted:
                return
        page, accepted = QInputDialog.getInt(self, "Source section", "Page/section containing this entry", 1, 1, self.data["sections"])
        if not accepted:
            return
        table = self.tables[name]
        index = table.rowCount()
        table.insertRow(index)
        for column, key in enumerate(self.keys[name]):
            value = str(page) if key == "page" else field if key == "name" else "Manually entered during review" if key == "evidence" else ""
            item = QTableWidgetItem(value)
            if key in {"page", "name", "evidence"}:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(index, column, item)
        table.selectRow(index)

    def remove_row(self):
        name = list(self.tables)[self.tabs.currentIndex()]
        table = self.tables[name]
        if name != "summaries" and table.currentRow() >= 0:
            table.removeRow(table.currentRow())


class DocumentsWidget(QWidget):
    ai_settings_changed = Signal()

    def __init__(self, database_path, storage_root=None, parent=None):
        super().__init__(parent)
        self.database_path = database_path
        self.storage_root = Path(storage_root) if storage_root is not None else DEFAULT_STORAGE_ROOT
        self.demo_mode = False
        self.processing_enabled = True
        self.task = None
        self._auto_pending = False
        self._stop_requested = False
        self._closing = False
        self._sources = []
        config = store.settings(database_path)
        self.provider_combo = QComboBox()
        self.provider_combo.addItem("Online — Vercel AI Gateway", "vercel")
        self.provider_combo.addItem("Online — OpenAI", "openai")
        self.provider_combo.addItem("Online — compatible API", "compatible")
        self.provider_combo.addItem("Local — Ollama (optional)", "local")
        self.provider_combo.setCurrentIndex(max(0, self.provider_combo.findData(config["provider"])))
        self._provider = config["provider"]
        endpoint_provider = {OPENAI_ENDPOINT: "openai", VERCEL_ENDPOINT: "vercel"}.get(config["online_endpoint"], "compatible")
        last_online = self._provider if self._provider != "local" else endpoint_provider
        self._last_online = last_online
        self._models = {"local": config["model"], "openai": config["online_model"] if last_online == "openai" else DEFAULT_ONLINE_MODEL,
            "vercel": config["online_model"] if last_online == "vercel" else VERCEL_MODEL,
            "compatible": config["online_model"] if last_online == "compatible" else ""}
        self._online_options = {
            "vercel": (VERCEL_ENDPOINT, config["api_key_env"] if last_online == "vercel" else VERCEL_KEY_ENV,
                config["embedding_model"] if last_online == "vercel" else VERCEL_EMBEDDING_MODEL),
            "openai": (OPENAI_ENDPOINT, config["api_key_env"] if last_online == "openai" else "OPENAI_API_KEY",
                config["embedding_model"] if last_online == "openai" else "text-embedding-3-small"),
            "compatible": (config["online_endpoint"] if last_online == "compatible" else "https://api.example.com/v1/chat/completions",
                config["api_key_env"] if last_online == "compatible" else "COMPATIBLE_AI_API_KEY",
                config["embedding_model"] if last_online == "compatible" else "text-embedding-3-small"),
        }
        self.model_input = QLineEdit(self._models[self._provider])
        self.endpoint_input = QLineEdit(config["online_endpoint"])
        self.api_key_env_input = QLineEdit(config["api_key_env"])
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText("Optional: paste key to save in OS keyring; never stored in SQLite")
        self.embedding_input = QLineEdit(config["embedding_model"])
        self.replies_checkbox = QCheckBox("Use this online provider for FAQ matching and reply suggestions too")
        self.replies_checkbox.setChecked(bool(config["online_replies"]))
        self.workbook_input = QLineEdit(config["workbook_path"] or str(Path(database_path).resolve().parent / "exports" / "crm_documents.xlsx"))
        self.choose_workbook_button = QPushButton("Choose workbook")
        self.choose_workbook_button.clicked.connect(self.choose_workbook)
        self.auto_checkbox = QCheckBox("Automatically read new and changed files")
        self.auto_checkbox.setToolTip("After storing files or importing mail, use the saved provider to update the workbook while CRM is open.")
        self.auto_checkbox.setChecked(bool(config["auto_extract"]))
        self.save_button = QPushButton("Save settings")
        self.save_button.clicked.connect(self.save_settings)
        self.process_button = QPushButton("Read files and update Excel")
        self.process_button.setObjectName("primaryButton")
        self.process_button.clicked.connect(self.process_files)
        self.export_button = QPushButton("Export saved results")
        self.export_button.clicked.connect(self.export_saved)
        self.cancel_button = QPushButton("Stop after current section")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        self.open_workbook_button = QPushButton("Open Excel workbook")
        self.open_workbook_button.clicked.connect(self.open_workbook)
        self.review_button = QPushButton("Review selected extraction")
        self.review_button.clicked.connect(self.review_selected)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.provider_note = QLabel()
        self.provider_note.setWordWrap(True)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Customer", "File", "Type", "Status", "Details"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.setMinimumHeight(130)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        content = QWidget()
        layout = QVBoxLayout(content)
        note = QLabel("One Excel workbook for all registered customer files: invoices, contracts and other documents. "
            "PDF scans, images, DOCX, XLSX and text are supported. Model output needs review; unsupported files remain listed.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.ai_settings_panel = QWidget()
        form = QFormLayout(self.ai_settings_panel)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("AI provider", self.provider_combo)
        form.addRow("Model", self.model_input)
        form.addRow(self.provider_note)
        self.online_settings = QWidget()
        online_form = QFormLayout(self.online_settings)
        online_form.setContentsMargins(0, 0, 0, 0)
        online_form.addRow("API key (optional)", self.api_key_input)
        online_form.addRow(self.replies_checkbox)
        self.advanced_online = QWidget()
        advanced_form = QFormLayout(self.advanced_online)
        advanced_form.setContentsMargins(0, 0, 0, 0)
        advanced_form.addRow("API endpoint", self.endpoint_input)
        advanced_form.addRow("API key variable", self.api_key_env_input)
        advanced_form.addRow("FAQ embedding model", self.embedding_input)
        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText("Advanced online settings")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.toggled.connect(self.advanced_online.setVisible)
        online_form.addRow(self.advanced_toggle)
        online_form.addRow(self.advanced_online)
        self.advanced_online.hide()
        form.addRow(self.online_settings)
        layout.addWidget(self.ai_settings_panel)
        self.workbook_settings_panel = QWidget()
        workbook_form = QFormLayout(self.workbook_settings_panel)
        workbook_form.setContentsMargins(0, 0, 0, 0)
        workbook_row = QHBoxLayout()
        workbook_row.addWidget(self.workbook_input, 1)
        workbook_row.addWidget(self.choose_workbook_button)
        workbook_form.addRow("Shared Excel file", workbook_row)
        layout.addWidget(self.workbook_settings_panel)
        layout.addWidget(self.auto_checkbox)
        buttons = QHBoxLayout()
        for button in (self.save_button, self.process_button, self.export_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        layout.addWidget(self.status_label)
        layout.addWidget(self.table, 1)
        footer = QHBoxLayout()
        for button in (self.review_button, self.open_workbook_button, self.cancel_button):
            footer.addWidget(button)
        layout.addLayout(footer)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.provider_combo.currentIndexChanged.connect(self.provider_changed)
        self.provider_changed()
        self.refresh()

    def provider_changed(self):
        chosen = self.provider_combo.currentData()
        changed = chosen != self._provider
        if changed:
            self._models[self._provider] = self.model_input.text().strip()
            if self._provider != "local":
                self._online_options[self._provider] = (self.endpoint_input.text(), self.api_key_env_input.text(), self.embedding_input.text())
            self.model_input.setText(self._models[chosen])
            self.api_key_input.clear()
            if chosen != "local":
                endpoint, key_variable, embedding_model = self._online_options[chosen]
                self.endpoint_input.setText(endpoint)
                self.api_key_env_input.setText(key_variable)
                self.embedding_input.setText(embedding_model)
                self._last_online = chosen
            self._provider = chosen
        online = chosen != "local"
        self.online_settings.setVisible(online)
        if chosen == "compatible":
            self.advanced_toggle.setChecked(True)
        self.endpoint_input.setReadOnly(chosen in {"openai", "vercel"})
        if chosen == "openai":
            self.endpoint_input.setText(OPENAI_ENDPOINT)
        elif chosen == "vercel":
            self.endpoint_input.setText(VERCEL_ENDPOINT)
        if not online:
            self.replies_checkbox.setChecked(False)
        self.provider_note.setText("Online: document text and page images go to the selected API; usage charges apply. No local model is required." if online else
            "Optional local mode: document content stays on this machine; start Ollama before extraction.")
        self.status_label.setText("Use a saved OS key or the API key environment variable. Paste a new key above to save it securely." if online else
            "Start the optional local model with Start Document Model.cmd.")

    def set_demo_mode(self, enabled):
        self.demo_mode = enabled
        if enabled:
            self.provider_note.setText("Offline demo: simulated extraction only. Open CRM.cmd to use online or local models.")
            self.status_label.setText("Offline demo: extraction of the fictional examples is simulated. No local model or real account is contacted.")
            self.model_input.setEnabled(False)
            self.provider_combo.setEnabled(False)
            self.online_settings.setEnabled(False)

    def choose_workbook(self):
        path, _ = QFileDialog.getSaveFileName(self, "Choose shared Excel workbook", self.workbook_input.text(), "Excel workbook (*.xlsx)")
        if path:
            self.workbook_input.setText(path)

    def save_settings(self):
        try:
            self._models[self._provider] = self.model_input.text().strip()
            online_model = self._models[self._provider] if self._provider != "local" else self._models[self._last_online]
            replies = self.replies_checkbox.isChecked() and not self.demo_mode and self._provider != "local"
            if self._provider != "local":
                online = OnlineSettings(online_model, self._provider, self.endpoint_input.text().strip(), self.api_key_env_input.text().strip(), self.embedding_input.text().strip())
                if self.api_key_input.text() and not self.demo_mode:
                    save_api_key(online, self.api_key_input.text())
                    self.api_key_input.clear()
                if replies:
                    OnlineClient(online).require_key()
            store.save_settings(self.database_path, self._models["local"], self.workbook_input.text().strip(), self.auto_checkbox.isChecked(),
                provider=self._provider, online_model=online_model, online_endpoint=self.endpoint_input.text().strip(),
                api_key_env=self.api_key_env_input.text().strip(), embedding_model=self.embedding_input.text().strip(), online_replies=replies)
        except ValueError as error:
            self.status_label.setText(str(error))
            return False
        self.status_label.setText("Document settings saved. Automatic processing is " + ("enabled." if self.auto_checkbox.isChecked() else "off."))
        self.ai_settings_changed.emit()
        return True

    def _start(self, operation):
        if self.task is not None:
            return
        self._stop_requested = False
        self.task = DocumentTask(operation, self)
        self.task.progress.connect(self.status_label.setText)
        self.task.failed.connect(self.status_label.setText)
        self.task.succeeded.connect(self.completed)
        self.task.finished.connect(self.finished)
        self.process_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.provider_combo.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.review_button.setEnabled(False)
        self.task.start()

    def process_files(self):
        if not self.processing_enabled:
            return
        if self.task is not None or not self.save_settings():
            return
        try:
            provider = document_provider(store.settings(self.database_path), demo_mode=self.demo_mode)
        except DocumentError as error:
            self.status_label.setText(str(error))
            return
        destination = self.workbook_input.text().strip()
        self._start(lambda worker: process_documents(self.database_path, self.storage_root, provider,
            progress=worker.progress.emit, cancelled=worker.isInterruptionRequested, workbook_path=destination))

    def process_automatically(self):
        if self._closing or not self.processing_enabled:
            return
        config = store.settings(self.database_path)
        if not config["auto_extract"]:
            self._auto_pending = False
            return
        if self.task is not None:
            if not self._stop_requested and not self.task.isInterruptionRequested():
                self._auto_pending = True
            return
        self._auto_pending = False
        provider = document_provider(config, demo_mode=self.demo_mode)
        destination = config["workbook_path"] or self.workbook_input.text()
        self._start(lambda worker: process_documents(self.database_path, self.storage_root, provider,
            progress=worker.progress.emit, cancelled=worker.isInterruptionRequested, workbook_path=destination))

    def export_saved(self):
        if self.task is not None:
            return
        destination = self.workbook_input.text().strip()
        self._start(lambda worker: export_workbook(self.database_path, destination, self.storage_root))

    def completed(self, result):
        if isinstance(result, Path):
            self.status_label.setText("Excel workbook updated from saved results.")
        else:
            self.status_label.setText(f"{result.extracted} extracted; {result.reused} reused; {result.skipped} unchanged; {result.failed} failed. " +
                ("Stopped; saved extraction results remain available for export." if result.cancelled else "Shared Excel workbook updated. Review the extracted data."))
        self.refresh()

    def finished(self):
        old, self.task = self.task, None
        stopped = self._stop_requested or bool(old and old.isInterruptionRequested())
        if old:
            old.deleteLater()
        self.process_button.setEnabled(self.processing_enabled)
        self.export_button.setEnabled(True)
        self.save_button.setEnabled(True)
        self.provider_combo.setEnabled(not self.demo_mode)
        self.cancel_button.setEnabled(False)
        self.refresh()
        if self._auto_pending and not stopped and not self._closing:
            QTimer.singleShot(0, self.process_automatically)
        else:
            self._auto_pending = False

    def cancel(self):
        if self.task:
            self._stop_requested = True
            self._auto_pending = False
            self.task.requestInterruption()
            self.status_label.setText("Stopping after the current local-model request finishes…")

    def prepare_to_close(self):
        self._closing = True
        self._auto_pending = False

    def refresh(self):
        self._sources = store.sources(self.database_path)
        self.table.setRowCount(len(self._sources))
        for index, source in enumerate(self._sources):
            data = json.loads(source["data_json"]) if source["data_json"] else None
            values = (source["customer_name"], source["filename"], data["document_type"] if data else source["file_type"],
                (source["extraction_status"] or "not_processed").replace("_", " "), source["error_code"] or ("; ".join(data["warnings"]) if data else "Not extracted yet"))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(index, column, item)
        self.selection_changed()

    def selection_changed(self):
        row = self.table.currentRow()
        source = self._sources[row] if 0 <= row < len(self._sources) else None
        self.review_button.setEnabled(bool(source and source["data_json"] and self.task is None))

    def review_selected(self):
        row = self.table.currentRow()
        if not 0 <= row < len(self._sources):
            return
        source = self._sources[row]
        if not source["data_json"]:
            return
        dialog = ExtractionReviewDialog(source, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            review_extraction(self.database_path, source, dialog.data, self.storage_root, approve=dialog.approve)
        except ValueError as error:
            self.status_label.setText(str(error))
            return
        self.export_saved()

    def open_workbook(self):
        path = Path(self.workbook_input.text().strip())
        if path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
        else:
            self.status_label.setText("Create the workbook first with Read files or Export saved results.")
