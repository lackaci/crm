"""An isolated test app with one setup page and session workflow approval."""
from contextlib import closing
from uuid import uuid4

from PySide6.QtCore import QTimer, QSignalBlocker, Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QPushButton, QCheckBox, QComboBox, QSpinBox, QLineEdit, QPlainTextEdit, QScrollArea)

from app.db.database import open_database
from app.db.customers import add_customer
from app.db.emails import import_emails
from app.db import automation as automation_store
from app.providers.email_mock import MockEmailProvider
from app.providers.llm_fake import FakeLLMProvider
from app.providers.factory import email_provider
from app.test_workflow import require_test_workspace, approve_workflow, run_workflow
from .main_window import MainWindow
from .background import BackgroundTask


class TestSetup(QWidget):
    __test__ = False

    def __init__(self, window):
        super().__init__()
        self.window = window
        outer = QVBoxLayout(self)
        note = QLabel("Set everything up here, then approve one workflow for this session. Test data is stored separately from the normal CRM.")
        note.setWordWrap(True)
        outer.addWidget(note)
        self.offline_checkbox = QCheckBox("Offline simulation — no Gmail or AI calls")
        self.offline_checkbox.setChecked(True)
        outer.addWidget(self.offline_checkbox)
        self.status = QLabel("Ready for an offline test. Choose a workflow below and click Approve and run.")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        outer.addWidget(self.status)
        content = QWidget()
        sections = QVBoxLayout(content)
        ai = QGroupBox("1. AI and document settings")
        ai_layout = QVBoxLayout(ai)
        documents = window.files_widget.documents_widget
        ai_layout.addWidget(documents.ai_settings_panel)
        ai_layout.addWidget(documents.workbook_settings_panel)
        ai_layout.addWidget(documents.auto_checkbox)
        documents.save_button.setText("Save AI and document settings")
        ai_layout.addWidget(documents.save_button)
        self.ai_status = QLabel("Offline uses the sample-answer helper. Turn off Offline simulation to use the selected online model.")
        self.ai_status.setWordWrap(True)
        ai_layout.addWidget(self.ai_status)
        documents.save_button.clicked.connect(lambda: self.ai_status.setText(documents.status_label.text()))
        sections.addWidget(ai)
        self.ai_group = ai

        gmail = QGroupBox("2. Gmail import and sending permissions")
        gmail_layout = QVBoxLayout(gmail)
        control = window.settings_widget.gmail_widget
        control.connect_button.setText("Connect Gmail and import")
        control.send_checkbox.setText("Allow sending through the session-approved test workflow")
        gmail_layout.addWidget(control)
        self.import_button = QPushButton("Import emails now")
        self.import_button.clicked.connect(window.import_test_mail)
        gmail_layout.addWidget(self.import_button)
        self.import_status = QLabel()
        self.import_status.setWordWrap(True)
        gmail_layout.addWidget(self.import_status)
        sections.addWidget(gmail)

        data = QGroupBox("3. Test customer and email")
        data_layout = QFormLayout(data)
        self.name_input = QLineEdit("Test customer")
        self.email_input = QLineEdit("anna@example.com")
        self.subject_input = QLineEdit("Opening hours?")
        self.body_input = QPlainTextEdit("What are your opening hours?")
        self.body_input.setMaximumHeight(85)
        data_layout.addRow("Customer name", self.name_input)
        data_layout.addRow("Customer / sender email", self.email_input)
        self.customer_button = QPushButton("Add customer")
        self.customer_button.clicked.connect(self.add_customer)
        data_layout.addRow(self.customer_button)
        data_layout.addRow("Email subject", self.subject_input)
        data_layout.addRow("Email text", self.body_input)
        self.email_button = QPushButton("Add sample email")
        self.email_button.clicked.connect(self.add_email)
        data_layout.addRow(self.email_button)
        answers = QPushButton("Edit approved saved answers")
        answers.clicked.connect(lambda: window.navigate(3))
        data_layout.addRow(answers)
        sections.addWidget(data)

        workflow = QGroupBox("4. Approve the whole workflow")
        flow = QFormLayout(workflow)
        self.mode_combo = QComboBox()
        for label, value in (("Prepare replies only", "prepare"), ("Create Gmail drafts", "draft"), ("Prepare and send replies", "send")):
            self.mode_combo.addItem(label, value)
        self.mode_combo.setCurrentIndex(2)
        self.batch_input, self.budget_input = QSpinBox(), QSpinBox()
        self.batch_input.setRange(1, 50)
        self.batch_input.setValue(5)
        self.budget_input.setRange(5, 200)
        self.budget_input.setValue(20)
        self.batch_input.valueChanged.connect(self.budget_input.setMinimum)
        self.repeat_checkbox = QCheckBox("Repeat import and workflow while this window is open")
        self.interval_input = QSpinBox()
        self.interval_input.setRange(30, 3600)
        self.interval_input.setValue(120)
        flow.addRow("Action", self.mode_combo)
        flow.addRow("Messages per run", self.batch_input)
        flow.addRow("Maximum messages this approval", self.budget_input)
        flow.addRow(self.repeat_checkbox)
        flow.addRow("Repeat interval (seconds)", self.interval_input)
        self.scope_label = QLabel()
        self.scope_label.setWordWrap(True)
        self.scope_label.setTextFormat(Qt.TextFormat.PlainText)
        actions = QHBoxLayout()
        self.approve_button = QPushButton("Approve and run workflow")
        self.approve_button.setObjectName("primaryButton")
        self.run_button = QPushButton("Run again")
        self.run_button.setEnabled(False)
        self.stop_button = QPushButton("Stop / revoke approval")
        self.approve_button.clicked.connect(window.approve_and_run)
        self.run_button.clicked.connect(window.run_approved)
        self.stop_button.clicked.connect(window.stop_workflow)
        for button in (self.approve_button, self.run_button, self.stop_button):
            actions.addWidget(button)
        sections.addWidget(workflow)
        jump = QHBoxLayout()
        self.section_buttons = {}
        for name, group in (("AI", ai), ("Gmail", gmail), ("Test emails", data), ("Workflow", workflow)):
            button = QPushButton(name)
            button.clicked.connect(lambda checked=False, group=group: scroll.verticalScrollBar().setValue(group.y()))
            self.section_buttons[name] = button
            jump.addWidget(button)
        outer.addLayout(jump)
        self.results = QPlainTextEdit()
        self.results.setReadOnly(True)
        self.results.setMinimumHeight(125)
        with closing(open_database(window.database_path)) as connection:
            for row in connection.execute("SELECT email_id,outcome,reason FROM desktop_test_results ORDER BY updated_at DESC LIMIT 30"):
                self.results.appendPlainText(f"Previous: email #{row['email_id']}: {row['outcome'].replace('_', ' ')}. {row['reason']}")
        sections.addWidget(QLabel("Workflow results"))
        sections.addWidget(self.results)
        self.output_preview = QPlainTextEdit()
        self.output_preview.setReadOnly(True)
        self.output_preview.setMinimumHeight(150)
        sections.addWidget(QLabel("Generated replies (including simulated and sent replies)"))
        sections.addWidget(self.output_preview)
        self.refresh_outputs()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        outer.addWidget(self.scope_label)
        outer.addLayout(actions)
        for widget in (documents.ai_settings_panel, documents.workbook_settings_panel, control):
            for field in widget.findChildren(QLineEdit):
                field.textChanged.connect(lambda *_: window.revoke_workflow("Settings changed. Approve the workflow again."))
            for field in widget.findChildren(QComboBox):
                field.currentIndexChanged.connect(lambda *_: window.revoke_workflow("Settings changed. Approve the workflow again."))
            for field in widget.findChildren(QCheckBox):
                field.toggled.connect(lambda *_: window.revoke_workflow("Settings changed. Approve the workflow again."))
        for signal in (self.mode_combo.currentIndexChanged, self.batch_input.valueChanged,
                       self.budget_input.valueChanged, self.repeat_checkbox.toggled, self.interval_input.valueChanged):
            signal.connect(lambda *_: window.revoke_workflow("Workflow options changed. Approve again."))
        self.offline_checkbox.toggled.connect(window.set_offline)
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.sync_status)
        self.status_timer.start(300)
        self.sync_status()

    def sync_status(self):
        window = self.window
        busy = window.workflow_task is not None or window.inbox_widget.import_task is not None or window.settings_widget.gmail_widget.task is not None
        self.approve_button.setEnabled(not busy)
        self.run_button.setEnabled(bool(not busy and window.workflow_approval and not window.workflow_approval.revoked.is_set() and window.workflow_approval.remaining > 0))
        self.import_status.setText(window.inbox_widget.status_label.text())
        if self.offline_checkbox.isChecked():
            scope = "OFFLINE SIMULATION: no real messages are sent."
        else:
            scope = "Account: " + (window.config.test_mailbox or "not connected") + "\nAllowed sending recipients: " + (", ".join(window.config.send_allowlist) or "none")
        action = self.mode_combo.currentText()
        self.scope_label.setText(scope + f"\n{action}: {self.batch_input.value()} messages/run, maximum {self.budget_input.value()} this approval. Closing or changing settings revokes approval.")
        self.approve_button.setText("Approve and simulate workflow" if self.offline_checkbox.isChecked() else
                                   "Approve and send replies" if self.mode_combo.currentData() == "send" else "Approve and run workflow")

    def refresh_outputs(self):
        with closing(open_database(self.window.database_path)) as connection:
            rows = connection.execute("SELECT r.email_id,r.outcome,r.reason,d.body,e.from_email FROM desktop_test_results r "
                                      "JOIN emails e ON e.id=r.email_id LEFT JOIN draft_replies d ON d.id=r.draft_id "
                                      "ORDER BY r.updated_at DESC,r.email_id DESC LIMIT 30").fetchall()
        self.output_preview.setPlainText("\n\n".join(f"Email #{row['email_id']}: {row['outcome'].replace('_', ' ')}\nTo: {row['from_email']}\n{row['body'] or row['reason']}" for row in rows)
                                         or "Run a workflow to see generated replies here.")

    def add_customer(self):
        try:
            add_customer(self.window.database_path, self.name_input.text().strip(), self.email_input.text().strip())
            self.window.customers_widget.refresh()
            self.window.inbox_widget.refresh_unmatched()
            self.status.setText("Customer added. Incoming mail from that email can now be linked.")
        except Exception:
            self.status.setText("Customer could not be added. Check the fields; the address may already exist.")

    def add_email(self):
        owner = self
        class Sample:
            def fetch_new_emails(self):
                return [{"id": "test-" + str(uuid4()), "from_name": owner.name_input.text().strip(),
                         "from_email": owner.email_input.text().strip(), "subject": owner.subject_input.text().strip(),
                         "body": owner.body_input.toPlainText()}]
        try:
            import_emails(self.window.database_path, Sample(), storage_root=self.window.files_widget.storage_root)
            self.window.inbox_widget.refresh_unmatched()
            self.status.setText("Sample email added to the test Inbox. It is not a real Gmail message.")
        except Exception:
            self.status.setText("Fill in sender name, email, subject and body before adding a sample.")


class TestWindow(MainWindow):
    use_email_workflow = False
    __test__ = False
    page_names = (*MainWindow.page_names, "Test setup")
    page_help = (*MainWindow.page_help, "AI, Gmail, test emails and session workflow approval in one place.")

    def __init__(self, database_path, **options):
        require_test_workspace(database_path)
        self.test_offline = True
        self.workflow_approval = None
        self.workflow_task = None
        self._run_after_import = False
        self._close_after_workflow = False
        automation_store.save_settings(database_path, enabled=False, auto_draft=False, poll_seconds=120)
        super().__init__(database_path, **options)
        self._live_provider = self.inbox_widget.provider
        self.test_setup = TestSetup(self)
        self.pages.addWidget(self.test_setup)
        self.navigation.item(5).setHidden(True)
        self.navigation.item(6).setHidden(True)
        self.navigation.item(7).setText("Test setup")
        self.gmail_button.setText("Test setup")
        self.mode_label.setText("Separate test workspace")
        self.setWindowTitle("Mini CRM - Test workspace")
        self.repeat_timer = QTimer(self)
        self.repeat_timer.timeout.connect(self.run_approved)
        self.inbox_widget.imported.connect(self._test_import_complete)
        self.approval_widget.gmail_draft_button.hide()
        self.approval_widget.approve_button.hide()
        self.files_widget.documents_widget.ai_settings_changed.connect(lambda: self.revoke_workflow("AI settings saved. Approve the new workflow."))
        self.set_offline(True)
        self.navigate(7)

    def navigate(self, index):
        super().navigate(7 if index in (5, 6) and hasattr(self, "test_setup") else index)

    def _configure_automation_timer(self):
        if hasattr(self, "automation_timer"):
            self.automation_timer.stop()

    def _apply_ai_settings(self):
        if getattr(self, "test_offline", True):
            provider = FakeLLMProvider()
            self.inbox_widget.llm_provider = self.faq_widget.llm_provider = self.automation_widget.llm = provider
        else:
            super()._apply_ai_settings()

    def _apply_gmail_connection(self, config, provider):
        self._import_after_connection = False
        super()._apply_gmail_connection(config, provider)
        self._live_provider = provider
        if hasattr(self, "test_setup"):
            self.revoke_workflow("Gmail connected. Importing your test mail...")
            QTimer.singleShot(0, self.import_test_mail)

    def _mail_pause_changed(self, paused):
        super()._mail_pause_changed(paused)
        if hasattr(self, "test_setup"):
            self.safety_label.setText("Mail activity paused" if paused else "Offline simulation: no real sends" if self.test_offline else "Live test: session approval required")

    def set_offline(self, enabled):
        if any(task is not None for task in (self.workflow_task, self.inbox_widget.import_task,
               self.files_widget.documents_widget.task, self.settings_widget.gmail_widget.task,
               self.inbox_widget.draft_task, self.faq_widget.test_task, self.approval_widget.delivery_task)):
            blocker = QSignalBlocker(self.test_setup.offline_checkbox)
            self.test_setup.offline_checkbox.setChecked(self.test_offline)
            del blocker
            self.test_setup.status.setText("Wait for the current operation before switching offline mode.")
            return
        self.test_offline = enabled
        self.revoke_workflow("Offline simulation ready." if enabled else "Live testing selected. Save AI settings and connect Gmail below.")
        self.inbox_widget.provider = MockEmailProvider() if enabled else self._live_provider
        self.inbox_widget.set_demo_mode(enabled)
        self.approval_widget.demo_mode = enabled
        self.approval_widget.set_connection(self.config, self.inbox_widget.provider)
        self.settings_widget.gmail_widget.setEnabled(not enabled)
        self.test_setup.ai_group.setEnabled(not enabled)
        self.test_setup.ai_status.setText("Offline uses the sample-answer helper. Turn off Offline simulation to use the selected online model." if enabled else
                                          "Save AI settings here. Enable the online FAQ/reply option if you want model-generated suggestions during testing.")
        self.test_setup.email_button.setEnabled(enabled or self.config.provider != "gmail")
        documents = self.files_widget.documents_widget
        documents.processing_enabled = not enabled
        documents.process_button.setEnabled(not enabled)
        if enabled:
            documents.provider_note.setText("Offline simulation: AI calls and document reading are disabled. Turn off Offline simulation to use this model.")
        else:
            documents.provider_changed()
        self._apply_ai_settings()
        self.safety_label.setText("Offline simulation: no real sends" if enabled else "Live test: session approval required")
        self.test_setup.sync_status()

    def revoke_workflow(self, reason="Workflow approval revoked."):
        if self.workflow_approval:
            self.workflow_approval.revoked.set()
        self._run_after_import = False
        if hasattr(self, "repeat_timer"):
            self.repeat_timer.stop()
        if hasattr(self, "test_setup"):
            self.test_setup.run_button.setEnabled(False)
            self.test_setup.status.setText(reason)

    def stop_workflow(self):
        self.revoke_workflow("Stopped. No new workflow operations will start. An operation already accepted remotely may finish.")

    def import_test_mail(self):
        self.inbox_widget.request_gmail_import()
        self.test_setup.sync_status()

    def approve_and_run(self):
        if self.workflow_task is not None or self.inbox_widget.import_task is not None or self.inbox_widget.draft_task is not None or self.settings_widget.gmail_widget.task is not None:
            self.test_setup.status.setText("Wait for the current operation to finish before approving a new workflow.")
            return
        self.revoke_workflow()
        try:
            self.workflow_approval = approve_workflow(self.database_path, self.config, mode=self.test_setup.mode_combo.currentData(),
                offline=self.test_offline, batch_size=self.test_setup.batch_input.value(), session_limit=self.test_setup.budget_input.value())
        except ValueError as error:
            self.test_setup.status.setText(str(error))
            return
        self.test_setup.run_button.setEnabled(True)
        if self.test_setup.repeat_checkbox.isChecked():
            self.repeat_timer.start(self.test_setup.interval_input.value() * 1000)
        self.run_approved()

    def run_approved(self):
        if self.workflow_task is not None:
            return
        if not self.workflow_approval:
            self.test_setup.status.setText("Approve the workflow first.")
            return
        try:
            self.workflow_approval.check()
        except ValueError as error:
            self.revoke_workflow(str(error))
            return
        if not self.test_offline and self.config.provider == "gmail":
            self._run_after_import = True
            self.inbox_widget.start_import()
        else:
            self._start_workflow()

    def _test_import_complete(self):
        self.test_setup.sync_status()
        if self._run_after_import:
            self._run_after_import = False
            result = getattr(self.inbox_widget, "last_import_result", None)
            if result and result.error:
                self.revoke_workflow("Import could not complete. Fix the Gmail connection or import settings, then approve again.")
                return
            QTimer.singleShot(0, self._start_workflow)

    def _start_workflow(self):
        if self.workflow_task is not None or not self.workflow_approval or self.workflow_approval.revoked.is_set():
            return
        approval, provider, llm = self.workflow_approval, self.inbox_widget.provider, self.inbox_widget.llm_provider
        task = BackgroundTask(lambda: run_workflow(approval, provider, llm, task.progress.emit), self)
        self.workflow_task = task
        task.progress.connect(self.test_setup.results.appendPlainText)
        task.progress.connect(self.test_setup.status.setText)
        task.failed.connect(self.test_setup.status.setText)
        task.succeeded.connect(lambda result: self._automation_complete())
        task.finished.connect(self._workflow_finished)
        self.test_setup.run_button.setEnabled(False)
        self.test_setup.approve_button.setEnabled(False)
        task.start()

    def _workflow_finished(self):
        old, self.workflow_task = self.workflow_task, None
        old.deleteLater()
        active = bool(self.workflow_approval and not self.workflow_approval.revoked.is_set() and self.workflow_approval.remaining > 0)
        self.test_setup.run_button.setEnabled(active)
        self.test_setup.approve_button.setEnabled(True)
        self.test_setup.refresh_outputs()
        if not active:
            self.repeat_timer.stop()
        if self._close_after_workflow:
            self.close()

    def closeEvent(self, event):
        self.stop_workflow()
        if self.workflow_task is not None and self.workflow_task.isRunning():
            self._close_after_workflow = True
            event.ignore()
            return
        if hasattr(self, "test_setup"):
            self.test_setup.status_timer.stop()
        super().closeEvent(event)
