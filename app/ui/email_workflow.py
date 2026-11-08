"""One setup surface for the five-minute, per-message review workflow."""
from datetime import datetime
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLabel, QLineEdit, QPushButton, QCheckBox

from app.db import email_workflow as store
from app.providers.online_model import save_api_key
from app.reply import email_assistant
from .background import BackgroundTask


class EmailWorkflowPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window, self.path, self.task = window, window.database_path, None
        config = store.settings(self.path)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        note = QLabel("Gmail → check every 5 minutes → AI reply → your approval → Send reply.\nChecks run while this app is open, including when minimized. Replies are never sent automatically.")
        note.setWordWrap(True)
        layout.addWidget(note)
        group = QGroupBox("1. Email AI — Vercel AI Gateway")
        form = QFormLayout(group)
        self.model_input = QLineEdit(config["model"])
        self.key_env = QLineEdit(config["key_env"])
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.setPlaceholderText("Optional: save a key to Windows keyring, or use the environment variable below")
        self.free_only = QCheckBox("Free model only — never switch to a paid model")
        self.free_only.setChecked(bool(config["free_only"]))
        self.model_input.setReadOnly(self.free_only.isChecked())
        self.free_only.toggled.connect(self._free_changed)
        form.addRow("Reply model", self.model_input)
        form.addRow(self.free_only)
        form.addRow("Gateway API key", self.key_input)
        form.addRow("Key environment variable", self.key_env)
        warning = QLabel("Nemotron Free is for non-sensitive trial use only. NVIDIA logs prompts and outputs; do not upload personal/confidential data or use it for production. If unavailable, AI pauses without a paid fallback. No embeddings are purchased. Document AI keeps its separate settings under Files.")
        warning.setWordWrap(True)
        form.addRow(warning)
        buttons = QHBoxLayout()
        self.save_button, self.test_button = QPushButton("Save email AI"), QPushButton("Test AI connection")
        self.save_button.clicked.connect(self.save)
        self.test_button.clicked.connect(self.test)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.test_button)
        form.addRow(buttons)
        self.ai_status = QLabel("Save and test email AI before starting automatic replies.")
        self.ai_status.setWordWrap(True)
        self.ai_status.setTextFormat(Qt.TextFormat.PlainText)
        form.addRow(self.ai_status)
        layout.addWidget(group)
        group = QGroupBox("2. Automatic checks and human review")
        flow = QVBoxLayout(group)
        self.auto_draft = QCheckBox("Prepare replies automatically after importing")
        self.auto_draft.setChecked(bool(config["auto_draft"]))
        flow.addWidget(self.auto_draft)
        self.auto_draft.toggled.connect(self._change_auto)
        buttons = QHBoxLayout()
        self.start_button, self.pause_button, self.check_button = QPushButton("Start 5-minute checks"), QPushButton("Pause checks"), QPushButton("Check Gmail now")
        self.start_button.clicked.connect(self.start)
        self.pause_button.clicked.connect(self.pause)
        self.check_button.clicked.connect(self.check_now)
        for button in (self.start_button, self.pause_button, self.check_button):
            buttons.addWidget(button)
        flow.addLayout(buttons)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        flow.addWidget(self.status)
        review = QPushButton("Open replies to review")
        review.clicked.connect(lambda: window.navigate(2))
        flow.addWidget(review)
        layout.addWidget(group)
        layout.addWidget(QLabel("3. Gmail account, import scope and sending permissions"))
        self.refresh()
        if window.demo_mode:
            for widget in (self.key_input, self.save_button, self.test_button, self.start_button, self.check_button):
                widget.setEnabled(False)
            self.ai_status.setText("Offline demo: Prepare reply uses fictional approved answers. Open CRM to configure Gmail and online AI.")

    def _free_changed(self, checked):
        self.model_input.setReadOnly(checked)
        if checked:
            self.model_input.setText(store.FREE_MODEL)

    def _change_auto(self, checked):
        store.update(self.path, auto_draft=bool(checked))

    def save(self):
        try:
            store.save_ai(self.path, self.model_input.text().strip(), self.key_env.text().strip(), self.free_only.isChecked())
            if self.key_input.text().strip():
                save_api_key(email_assistant.online_settings(store.settings(self.path)), self.key_input.text())
                self.key_input.clear()
            self.window._configure_automation_timer()
            self.ai_status.setText("Saved. Test AI connection to validate this model and resume paused drafting.")
            return True
        except (ValueError, RuntimeError) as error:
            self.ai_status.setText(str(error))
            return False

    def test(self):
        if self.task is not None or not self.save():
            return
        self.task = BackgroundTask(lambda: email_assistant.test_connection(self.path), self)
        self.task.succeeded.connect(self.ai_status.setText)
        self.task.failed.connect(self.ai_status.setText)
        self.task.finished.connect(self._test_finished)
        self.test_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.ai_status.setText("Testing Vercel with a fictional question...")
        self.task.start()

    def _test_finished(self):
        old, self.task = self.task, None
        old.deleteLater()
        self.test_button.setEnabled(True)
        self.save_button.setEnabled(True)
        self.refresh()

    def start(self):
        if self.window.demo_mode:
            return
        if self.window.config.provider != "gmail":
            self.status.setText("Connect your Gmail account below first, then Start checks.")
            self.window.settings_widget.gmail_widget.account_input.setFocus()
            return
        config = store.settings(self.path)
        if self.auto_draft.isChecked() and (not config["configured"] or config["tested_profile"] != store.profile(config)):
            self.status.setText("Save and Test AI connection first, or turn off automatic replies to import only.")
            return
        store.set_running(self.path, True, self.auto_draft.isChecked())
        self.window._configure_automation_timer()
        self.window._scheduled_import()
        self.refresh()

    def pause(self):
        self.window._manual_draft_drain = False
        store.set_running(self.path, False, self.auto_draft.isChecked())
        self.window._configure_automation_timer()
        self.refresh()

    def check_now(self):
        self.window.inbox_widget.request_gmail_import()
        self.refresh()

    def refresh(self):
        config = store.settings(self.path)
        counts = store.job_counts(self.path)
        clock = lambda value: datetime.fromtimestamp(value).strftime("%H:%M:%S") if value else "—"
        state = "Running every 5 minutes" if config["enabled"] else "Checks paused"
        tested = config['tested_profile'] == store.profile(config)
        summary = ('Gmail connected' if self.window.config.provider == 'gmail' else 'Gmail needs connection') + ' | ' + ('AI tested' if tested else 'AI needs test') + ' | Every 5 minutes | ' + ('Sending permission ready' if self.window.config.sending_enabled else 'Sending not enabled') + ' | Human approval required'
        if config["enabled"] and config["next_check"] and config["next_check"] <= time.time():
            state += " · check due (waiting for current operation)"
        self.status.setText(summary + '\n' + f"{state} · Last check {clock(config['last_check'])} · Next {clock(config['next_check'])}\n"
            f"Queued {counts.get('queued', 0)} · Preparing {counts.get('generating', 0)} · Ready for review {counts.get('ready', 0)} · Approved {counts.get('approved', 0)} · Needs information {counts.get('needs_info', 0)} · Failed {counts.get('failed', 0)}\n"
            + (config["last_result"] or "No check completed yet.") + ("\nAI paused: " + config["ai_error"] if config["ai_paused"] else ""))
        self.start_button.setEnabled(not self.window.demo_mode and not config["enabled"])
        self.pause_button.setEnabled(bool(config["enabled"]))
