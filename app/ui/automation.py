"""Simple schedule, staff rules and explainable history for desktop triage."""
from PySide6.QtCore import Signal, QSignalBlocker
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from app.automation import run_automation
from app.db import automation as store
from .background import BackgroundTask


class AutomationWidget(QWidget):
    settings_changed = Signal()
    completed = Signal()
    open_email = Signal(int, object)
    review_draft = Signal(int)

    def __init__(self, database_path, llm=None, demo_mode=False, parent=None):
        super().__init__(parent)
        self.database_path, self.llm, self.demo_mode = database_path, llm, demo_mode
        self.task = None
        self._events = []
        self.enabled_checkbox = QCheckBox("Enable inbox automation")
        self.draft_checkbox = QCheckBox("Prepare safe replies from approved saved answers")
        self.interval_input = QSpinBox()
        self.interval_input.setRange(30, 3600)
        self.interval_input.setSuffix(" seconds")
        self.save_button = QPushButton("Save automation settings")
        self.save_button.clicked.connect(self.save)
        self.run_button = QPushButton("Process imported mail now")
        self.run_button.setObjectName("primaryButton")
        self.run_button.clicked.connect(self.run_now)
        self.pause_button = QPushButton("Pause automation")
        self.pause_button.clicked.connect(self.pause)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        note = QLabel("While CRM is open, enabled automation checks connected Gmail on a schedule. "
            "Unknown senders need customer assignment. Replies always wait for your review. " +
            ("Demo processes fictional imported mail only; Gmail polling stays off." if demo_mode else ""))
        note.setWordWrap(True)
        schedule = QFormLayout()
        schedule.addRow(self.enabled_checkbox)
        schedule.addRow("Check Gmail every", self.interval_input)
        schedule.addRow(self.draft_checkbox)

        self.rules_panel = QWidget()
        rules_layout = QVBoxLayout(self.rules_panel)
        rules_layout.setContentsMargins(0, 0, 0, 0)
        self.staff_input = QLineEdit()
        self.staff_input.setPlaceholderText("Staff name")
        self.add_staff_button = QPushButton("Add staff member")
        self.add_staff_button.clicked.connect(self.add_staff)
        staff_row = QHBoxLayout()
        staff_row.addWidget(self.staff_input, 1)
        staff_row.addWidget(self.add_staff_button)
        rules_layout.addLayout(staff_row)
        rules_form = QFormLayout()
        self.rule_controls = {}
        for category in store.CATEGORIES:
            assignee, priority = QComboBox(), QComboBox()
            priority.addItems(["normal", "high"])
            row = QHBoxLayout()
            row.addWidget(assignee, 1)
            row.addWidget(priority)
            rules_form.addRow(category.title(), row)
            self.rule_controls[category] = (assignee, priority)
        rules_layout.addLayout(rules_form)
        staff_note = QLabel("Staff names are local routing labels. They do not create server login accounts. "
            "Rule changes apply to unprocessed mail; existing assignments stay in the history.")
        staff_note.setWordWrap(True)
        rules_layout.addWidget(staff_note)
        self.rules_toggle = QToolButton()
        self.rules_toggle.setText("Staff and assignment rules")
        self.rules_toggle.setCheckable(True)
        self.rules_toggle.toggled.connect(self.rules_panel.setVisible)
        self.rules_panel.hide()

        self.history_table = QTableWidget(0, 6)
        self.history_table.setHorizontalHeaderLabels(["Message", "Category", "Priority", "Assigned to", "Result", "Time"])
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.setWordWrap(False)
        self.history_table.verticalHeader().hide()
        self.history_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.history_table.itemSelectionChanged.connect(self.select_event)
        self.history_table.setMinimumHeight(130)
        self.reason_text = QPlainTextEdit()
        self.reason_text.setReadOnly(True)
        self.reason_text.setMaximumHeight(90)
        self.reason_text.setPlaceholderText("Select an event to see why automation took that action.")
        self.open_button = QPushButton("Open message")
        self.open_button.clicked.connect(self.open_selected)
        self.review_button = QPushButton("Review reply")
        self.review_button.clicked.connect(self.review_selected)
        self.refresh_button = QPushButton("Refresh history")
        self.refresh_button.clicked.connect(self.refresh_history)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.addWidget(note)
        layout.addLayout(schedule)
        layout.addWidget(self.rules_toggle)
        layout.addWidget(self.rules_panel)
        buttons = QHBoxLayout()
        for button in (self.save_button, self.run_button, self.pause_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        layout.addWidget(self.status_label)
        layout.addWidget(QLabel("Automation history (latest 200 actions)"))
        layout.addWidget(self.history_table, 1)
        layout.addWidget(self.reason_text)
        actions = QHBoxLayout()
        for button in (self.open_button, self.review_button, self.refresh_button):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.load_settings()
        self.refresh_history()

    def load_settings(self):
        config = store.settings(self.database_path)
        self.enabled_checkbox.setChecked(bool(config["enabled"]))
        self.draft_checkbox.setChecked(bool(config["auto_draft"]))
        self.interval_input.setValue(config["poll_seconds"])
        self.load_rules()
        self.pause_button.setEnabled(bool(config["enabled"]))
        self.status_label.setText("Automation enabled. New work runs after imports and scheduled Gmail checks." if config["enabled"] else "Automation paused. Enable it and save to start.")

    def load_rules(self):
        rules = store.list_rules(self.database_path)
        staff = store.list_staff(self.database_path)
        for category, (assignee, priority) in self.rule_controls.items():
            assignee.clear()
            assignee.addItem("Unassigned", None)
            for member in staff:
                assignee.addItem(member["name"], member["id"])
            assignee.setCurrentIndex(max(0, assignee.findData(rules[category]["staff_id"])))
            priority.setCurrentText(rules[category]["priority"])

    def add_staff(self):
        try:
            store.add_staff(self.database_path, self.staff_input.text())
        except Exception as error:
            self.status_label.setText(str(error) if isinstance(error, ValueError) else "Could not add this staff member.")
            return
        selections = {category: (assignee.currentData(), priority.currentText()) for category, (assignee, priority) in self.rule_controls.items()}
        self.staff_input.clear()
        self.load_rules()
        for category, (staff_id, priority_value) in selections.items():
            assignee, priority = self.rule_controls[category]
            assignee.setCurrentIndex(max(0, assignee.findData(staff_id)))
            priority.setCurrentText(priority_value)
        self.status_label.setText("Staff member added. Choose their categories, then save automation settings.")

    def save(self):
        try:
            store.save_rules(self.database_path, {category: {"staff_id": assignee.currentData(), "priority": priority.currentText()}
                for category, (assignee, priority) in self.rule_controls.items()})
            store.save_settings(self.database_path, enabled=self.enabled_checkbox.isChecked(),
                auto_draft=self.draft_checkbox.isChecked(), poll_seconds=self.interval_input.value())
        except Exception:
            self.status_label.setText("Could not save automation settings. Check the staff rules and try again.")
            return
        self.load_settings()
        self.settings_changed.emit()

    def pause(self):
        config = store.settings(self.database_path)
        try:
            store.save_settings(self.database_path, enabled=False, auto_draft=bool(config["auto_draft"]), poll_seconds=config["poll_seconds"])
        except Exception:
            self.status_label.setText("Could not pause yet. Wait for the current database operation and try again.")
            return
        self.load_settings()
        self.settings_changed.emit()

    def run_now(self):
        if self.task is not None:
            return
        if not store.settings(self.database_path)["enabled"]:
            self.status_label.setText("Enable inbox automation and save settings first.")
            return
        self.task = BackgroundTask(lambda: run_automation(self.database_path, self.llm), self)
        self.task.succeeded.connect(self.succeeded)
        self.task.failed.connect(self.status_label.setText)
        self.task.finished.connect(self.finished)
        self.run_button.setEnabled(False)
        self.status_label.setText("Processing imported mail…")
        self.task.start()

    def succeeded(self, result):
        self.refresh_history()
        self.status_label.setText("Automation is paused; no work was performed." if result.paused else
            f"{result.processed} message(s) processed; {result.drafts} reply suggestion(s); {result.attention} need attention.")
        self.completed.emit()

    def finished(self):
        old, self.task = self.task, None
        if old:
            old.deleteLater()
        self.run_button.setEnabled(True)

    def refresh_history(self):
        selected = self.selected_event()
        selected_id = selected["id"] if selected else None
        blocker = QSignalBlocker(self.history_table)
        self._events = store.history(self.database_path)
        self.history_table.setRowCount(len(self._events))
        for index, event in enumerate(self._events):
            values = (event["subject"], event["category"].title(), event["priority"].title(),
                event["staff_name"] or "Unassigned", event["outcome"].replace("_", " ").title(), event["created_at"])
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.history_table.setItem(index, column, item)
        for column in range(1, 6):
            self.history_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.history_table.clearSelection()
        self.history_table.setCurrentCell(-1, -1)
        for index, event in enumerate(self._events):
            if event["id"] == selected_id:
                self.history_table.selectRow(index)
                break
        del blocker
        self.select_event()

    def selected_event(self):
        row = self.history_table.currentRow()
        return self._events[row] if 0 <= row < len(self._events) else None

    def select_event(self):
        event = self.selected_event()
        self.reason_text.setPlainText(event["reason"] if event else "")
        self.open_button.setEnabled(event is not None)
        self.review_button.setEnabled(bool(event and event["draft_id"] and event["draft_status"] == "pending"))

    def open_selected(self):
        event = self.selected_event()
        if event:
            self.open_email.emit(event["email_id"], event["customer_id"])

    def review_selected(self):
        event = self.selected_event()
        if event and event["draft_id"] and event["draft_status"] == "pending":
            self.review_draft.emit(event["draft_id"])
