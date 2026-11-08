"""A flat workspace organized around reading, preparing and reviewing replies."""
from pathlib import Path

from PySide6.QtCore import Qt, QSignalBlocker, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QListWidget,
    QMainWindow, QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from app.config import AppConfig, RunMode, load_config
from app.db.customers import list_customers
from .approval import ApprovalQueueWidget
from .customers import CustomersWidget
from .faq_view import FAQView
from .settings import SettingsWidget
from .automation import AutomationWidget
from app.db import automation as automation_store


class MainWindow(QMainWindow):
    """One menu, one shared customer, and a direct inbox-to-review handoff."""

    use_email_workflow = True
    page_names = ("Inbox", "Customers", "Review replies", "Saved answers", "Files", "Setup", "Automation")
    page_help = (
        "Refresh incoming mail, choose a quick filter, then read and act on a message.",
        "Find a customer on the left. Add or edit their details on the right.",
        "Read the message and source facts, edit if needed, then Approve reply and Send reply.",
        "Manage the answers that can be used to prepare replies. Approve an answer before using it.",
        "Choose a customer above to see and organize their files.",
        "Connect Gmail, test email AI, start five-minute checks and choose sending permissions.",
        "Process incoming mail, route it to staff and review what automation did.",
    )

    def __init__(self, database_path: Path, email_provider=None, llm_provider=None,
                 parent=None, storage_root: Path | None = None,
                 config: AppConfig | None = None, demo_mode: bool = False) -> None:
        super().__init__(parent)
        if self.use_email_workflow:
            self.page_names = (*self.page_names, 'Replied')
            self.page_help = (*self.page_help, 'Previously replied emails, with the original message and exact sent response.')
        self.replied_widget = None
        self.database_path = database_path
        self.demo_mode = demo_mode
        self._import_after_connection = False
        self.email_draft_task = None
        self._manual_draft_drain = False
        self.email_setup = None
        config = config or load_config()
        self.config = config
        self.customers_widget = CustomersWidget(database_path, email_provider=email_provider,
            llm_provider=llm_provider, storage_root=storage_root, gmail_allowed_senders=config.gmail_allowed_senders)
        self.inbox_widget = self.customers_widget.inbox_widget
        self.inbox_widget.set_demo_mode(demo_mode)
        self._default_llm = self.inbox_widget.llm_provider
        self.files_widget = self.customers_widget.files_widget
        self.files_widget.documents_widget.set_demo_mode(demo_mode)
        self.approval_widget = ApprovalQueueWidget(database_path, provider=email_provider, sending_enabled=False)
        self.approval_queue = self.approval_widget
        self.approval_widget.demo_mode = demo_mode
        self.approval_widget.set_connection(config, self.inbox_widget.provider)
        self.faq_widget = FAQView(database_path)
        self.settings_widget = SettingsWidget(config, database_path=database_path, demo_mode=demo_mode)
        self.automation_widget = AutomationWidget(database_path, self.inbox_widget.llm_provider, demo_mode=demo_mode)
        self.privacy_preview_widget = self.settings_widget.privacy_preview_widget

        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.setAccessibleName("Workspace navigation")
        self.navigation.addItems(self.page_names)
        for index in range(len(self.page_names)):
            self.navigation.item(index).setToolTip(f"{self.page_names[index]} (Ctrl+{index + 1})")
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self)
            shortcut.activated.connect(lambda index=index: self.navigate(index))
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(190)
        side_layout = QVBoxLayout(sidebar)
        brand = QLabel("Mini CRM")
        brand.setObjectName("brand")
        side_layout.addWidget(brand)
        self.mode_label = QLabel("Demo workspace" if demo_mode else "Customer workspace")
        self.mode_label.setObjectName("mutedLabel")
        side_layout.addWidget(self.mode_label)
        side_layout.addSpacing(20)
        side_layout.addWidget(self.navigation, 1)
        self.more_tools_button = QPushButton("More tools")
        self.more_tools_button.setToolTip('Show customers, saved answers, files and advanced automation.')
        self.more_tools_button.setCheckable(True)
        self.more_tools_button.setVisible(self.use_email_workflow)
        self.more_tools_button.toggled.connect(self._show_more_tools)
        side_layout.addWidget(self.more_tools_button)
        if self.use_email_workflow:
            self._show_more_tools(False)
        safety = self.safety_label = QLabel("Sending is disabled")
        safety.setWordWrap(True)
        safety.setObjectName("mutedLabel")
        side_layout.addWidget(safety)
        if demo_mode:
            demo_note = QLabel("Fictional data. Changes disappear when you close this demo.")
            demo_note.setWordWrap(True)
            side_layout.addWidget(demo_note)

        self.page_title = QLabel()
        self.page_title.setObjectName("pageTitle")
        self.help_label = QLabel()
        self.help_label.setWordWrap(True)
        self.help_label.setObjectName("mutedLabel")
        self.customer_selector = QComboBox()
        self.customer_selector.setAccessibleName("Current customer")
        self.customer_selector.setMinimumWidth(220)
        self.customer_context = QWidget()
        context_layout = QHBoxLayout(self.customer_context)
        context_layout.setContentsMargins(0, 5, 0, 5)
        context_layout.addWidget(QLabel("Customer"))
        context_layout.addWidget(self.customer_selector, 1)
        self.manage_customers_button = QPushButton("Manage customers")
        self.manage_customers_button.clicked.connect(lambda: self.navigate(1))
        context_layout.addWidget(self.manage_customers_button)

        self.pages = QStackedWidget()
        self.tabs = self.pages  # Compatibility for callers that select pages programmatically.
        for widget in (self.inbox_widget, self.customers_widget, self.approval_widget,
                       self.faq_widget, self.files_widget, self.settings_widget, self.automation_widget):
            self.pages.addWidget(widget)
        if self.use_email_workflow:
            from .replied import RepliedEmailsWidget
            self.replied_widget = RepliedEmailsWidget(database_path)
            self.pages.addWidget(self.replied_widget)
        self.example_answers_button = QPushButton("Load example answers")
        self.example_answers_button.clicked.connect(self._load_example_answers)
        self.example_answers_button.setVisible(False)
        self._examples_available = config.mode is RunMode.MOCK
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(24, 20, 24, 20)
        heading = QHBoxLayout()
        heading.addWidget(self.page_title, 1)
        heading.addWidget(self.example_answers_button)
        body_layout.addLayout(heading)
        body_layout.addWidget(self.help_label)
        self.gmail_button = QPushButton("Gmail connection" if not demo_mode else "Gmail setup (open CRM to connect)")
        self.gmail_button.clicked.connect(lambda: self.navigate(5))
        heading.addWidget(self.gmail_button)
        body_layout.addSpacing(8)
        body_layout.addWidget(self.customer_context)
        body_layout.addWidget(self.pages, 1)
        shell = QWidget()
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        shell_layout.addWidget(sidebar)
        shell_layout.addWidget(body, 1)
        self.setCentralWidget(shell)
        self.setWindowTitle("Mini CRM — Demo" if demo_mode else "Mini CRM")
        self.resize(1100, 760)
        self.setMinimumSize(900, 620)
        self._apply_style()

        self.navigation.currentRowChanged.connect(self._open_page)
        self.customer_selector.currentIndexChanged.connect(self._choose_customer)
        self.customers_widget.customer_changed.connect(self._customer_changed)
        self.customers_widget.customers_changed.connect(self._reload_customers)
        self.inbox_widget.draft_created.connect(self._review_new_draft)
        self.inbox_widget.gmail_setup_requested.connect(self._connect_and_import_gmail)
        self.inbox_widget.import_options_requested.connect(self._change_gmail_import_options)
        self.inbox_widget.saved_answers_requested.connect(lambda: self.navigate(3))
        self.inbox_widget.email_assigned.connect(self._open_assigned_message)
        self.inbox_widget.unread_changed.connect(self._update_unread_count)
        self.approval_widget.queue_changed.connect(self._update_review_count)
        self.approval_widget.queue_changed.connect(self.inbox_widget.refresh)
        self.settings_widget.gmail_widget.connection_ready.connect(self._apply_gmail_connection)
        self.settings_widget.gmail_widget.paused_changed.connect(self._mail_pause_changed)
        self.automation_widget.settings_changed.connect(self._configure_automation_timer)
        self.automation_widget.completed.connect(self._automation_complete)
        self.automation_widget.review_draft.connect(self._review_new_draft)
        self.automation_widget.open_email.connect(self._open_automation_email)
        self.inbox_widget.imported.connect(self._automation_complete)
        self.files_widget.documents_widget.ai_settings_changed.connect(self._apply_ai_settings)
        self.automation_timer = QTimer(self)
        self.automation_timer.timeout.connect(self._scheduled_import)
        self._reload_customers()
        if self.customer_selector.count() > 1:
            self.customer_selector.setCurrentIndex(1)
        self.inbox_widget.scope_combo.setCurrentIndex(0)
        self.navigate(0)
        if demo_mode:
            from app.db.inbox import messages
            examples = messages(self.database_path, customer_id=self.customer_selector.currentData())
            if examples:
                self.inbox_widget.open_message(examples[0]["id"])
        if demo_mode:
            self.settings_widget.mode_combo.setEnabled(False)
            self.settings_widget.provider_combo.setEnabled(False)
            self.settings_widget.save_button.setEnabled(False)
        self._update_review_count()
        self._mail_pause_changed(self.settings_widget.gmail_widget.pause_checkbox.isChecked())
        self._configure_automation_timer()
        self._apply_ai_settings()
        if self.use_email_workflow:
            from .email_workflow import EmailWorkflowPanel
            self.email_setup = EmailWorkflowPanel(self)
            self.inbox_widget.pause_checks_button.clicked.connect(self.email_setup.pause)
            self.settings_widget.content_layout.insertWidget(0, self.email_setup)
            self.gmail_button.setText("Setup: Gmail and AI")
            self.approval_widget.enable_review_workflow()
            self.approval_widget.regenerate_requested.connect(self._regenerate_reply)
            self.inbox_widget.assisted_reply_requested.connect(self._prepare_assisted_reply)
            self.inbox_widget.use_review_workflow = True
            self._configure_automation_timer()
            self.workflow_heartbeat = QTimer(self)
            self.workflow_heartbeat.timeout.connect(self._workflow_heartbeat)
            self.workflow_heartbeat.start(1000)
            self._workflow_heartbeat()
            QTimer.singleShot(0, self._scheduled_import)

    def _apply_ai_settings(self):
        from app.db.documents import settings
        from app.providers.online_model import OnlineSettings
        from app.providers.llm_online import OnlineFAQProvider
        from app.providers.audit import database_audit_callback
        config = settings(self.database_path)
        provider = self._default_llm
        from app.db import email_workflow as workflow
        separate_email_ai = self.use_email_workflow and workflow.settings(self.database_path)['selected']
        if separate_email_ai:
            self.files_widget.documents_widget.replies_checkbox.setEnabled(False)
            self.files_widget.documents_widget.replies_checkbox.setToolTip('Email replies now use the independent model in Setup: Gmail and AI.')
        if not separate_email_ai and not self.demo_mode and config["online_replies"] and config["provider"] != "local":
            provider = OnlineFAQProvider(OnlineSettings.from_config(config), database_audit_callback(self.database_path))
        self.inbox_widget.llm_provider = provider
        self.automation_widget.llm = provider
        self.faq_widget.llm_provider = provider

    def _configure_automation_timer(self):
        if self.use_email_workflow:
            from app.db import email_workflow as workflow
            current = workflow.settings(self.database_path)
            if current["selected"]:
                self._apply_ai_settings()
                self.automation_timer.stop()
                if not self.demo_mode:
                    self.automation_timer.start(1000)
                return
        config = automation_store.settings(self.database_path)
        self.automation_timer.stop()
        if config["enabled"] and not self.demo_mode and self.config.provider == "gmail":
            self.automation_timer.start(config["poll_seconds"] * 1000)

    def _scheduled_import(self):
        from app.db.connections import mail_paused
        if self.use_email_workflow:
            from app.db import email_workflow as workflow
            current = workflow.settings(self.database_path)
            if self.email_setup:
                self.email_setup.refresh()
            if current["selected"]:
                if (self.demo_mode or self.config.provider != "gmail" or not current["enabled"]
                        or mail_paused(self.database_path) or self.inbox_widget.import_task is not None
                        or self.inbox_widget.draft_task is not None
                        or self.approval_widget.delivery_task is not None or self.automation_widget.task is not None
                        or self.settings_widget.gmail_widget.task is not None):
                    return
                if workflow.claim_poll(self.database_path):
                    self.inbox_widget.start_import(automatic=True)
                return
        config = automation_store.settings(self.database_path)
        if (self.demo_mode or self.config.provider != "gmail" or not config["enabled"] or mail_paused(self.database_path)
                or self.inbox_widget.import_task is not None or self.automation_widget.task is not None
                or self.inbox_widget.draft_task is not None or self.faq_widget.test_task is not None
                or self.approval_widget.delivery_task is not None or self.settings_widget.gmail_widget.task is not None):
            return
        self.inbox_widget.start_import(automatic=True)

    def _automation_complete(self):
        self.inbox_widget.refresh()
        self.inbox_widget.refresh_unmatched()
        self.approval_widget.refresh(preserve_editor=True)
        self.automation_widget.refresh_history()
        self.files_widget.documents_widget.refresh()
        self.files_widget.documents_widget.process_automatically()
        if self.email_setup:
            self.email_setup.refresh()
            if not self.inbox_widget.background_import:
                self._manual_draft_drain = True
            error = getattr(getattr(self.inbox_widget, 'last_import_result', None), 'error', None)
            if error and ('authorization' in error.lower() or 'authorize' in error.lower()):
                self.settings_widget.gmail_widget.status_label.setText('Reconnect Gmail below. ' + error)

    def _prepare_assisted_reply(self, email_id):
        if self.email_draft_task is not None or self.inbox_widget.import_task is not None:
            self.inbox_widget._reply_status("Reply preparation is already running. Your message is saved; try again when it finishes.")
            return
        from app.db import email_workflow as workflow
        workflow.enqueue(self.database_path, email_id=email_id)
        self._run_reply_jobs(email_id)

    def _workflow_heartbeat(self):
        if not self.email_setup:
            return
        from app.db import email_workflow as workflow
        from datetime import datetime
        self.email_setup.refresh()
        config = workflow.settings(self.database_path)
        clock = lambda value: datetime.fromtimestamp(value).strftime('%H:%M') if value else '—'
        gmail = 'Gmail connected' if self.config.provider == 'gmail' else 'Gmail not connected'
        from app.db.connections import mail_paused
        if mail_paused(self.database_path):
            gmail = 'Mail activity paused'
        import_error = getattr(getattr(self.inbox_widget, 'last_import_result', None), 'error', None)
        if import_error and ('authorization' in import_error.lower() or 'authorize' in import_error.lower()):
            gmail = 'Gmail needs reconnect'
        ai = 'AI paused' if config['ai_paused'] else 'AI ready' if config['tested_profile'] == workflow.profile(config) else 'AI needs setup'
        self.inbox_widget.workflow_summary.setText(f"{gmail} · Last check {clock(config['last_check'])} · Next {clock(config['next_check'])} · {ai} · " + ('5-minute checks running' if config['enabled'] else 'Checks paused'))
        self.inbox_widget.pause_checks_button.setVisible(bool(config['enabled']))
        if ((config['enabled'] or self._manual_draft_drain) and config['auto_draft'] and config['configured'] and not config['ai_paused']
                and self.config.provider == 'gmail' and not self.demo_mode
                and self.email_draft_task is None and self.inbox_widget.import_task is None
                and self.approval_widget.delivery_task is None and self.settings_widget.gmail_widget.task is None):
            from app.db.connections import mail_paused
            if not mail_paused(self.database_path) and workflow.jobs_due(self.database_path, self.config.test_mailbox):
                self._run_reply_jobs(None, background=True, scheduled=not self._manual_draft_drain)
            elif not mail_paused(self.database_path):
                self._manual_draft_drain = False

    def _regenerate_reply(self, draft_id):
        if self.email_draft_task is not None or self.inbox_widget.import_task is not None:
            self.approval_widget.status_label.setText("Wait for the current import or AI request to finish.")
            return
        from app.reply.email_assistant import regenerate
        try:
            email_id, previous_body = regenerate(self.database_path, draft_id)
            self._run_reply_jobs(email_id, previous_body)
        except ValueError as error:
            self.approval_widget.status_label.setText(str(error))

    def _run_reply_jobs(self, email_id, previous_body=None, background=False, scheduled=False):
        from .background import BackgroundTask
        from app.reply.email_assistant import run_jobs
        self._background_drafting = background
        if self.demo_mode:
            # Fictional local lookup is immediate; only online work needs a worker.
            self._assisted_reply_ready(run_jobs(self.database_path, email_id=email_id, limit=1,
                regenerate_body=previous_body, offline=True))
            return
        task = BackgroundTask(lambda: run_jobs(self.database_path, email_id=email_id, limit=5 if background else 1,
            regenerate_body=previous_body, offline=self.demo_mode, progress=task.progress.emit,
            mailbox=self.config.test_mailbox if background else None, automatic=scheduled), self)
        self.email_draft_task = task
        task.progress.connect(self.inbox_widget._reply_status)
        task.progress.connect(self.approval_widget.status_label.setText)
        task.succeeded.connect(self._assisted_reply_ready)
        task.failed.connect(self.inbox_widget._reply_status)
        task.finished.connect(self._reply_jobs_finished)
        self.inbox_widget.create_draft_button.setEnabled(False)
        self.approval_widget.regenerate_button.setEnabled(False)
        self.inbox_widget._reply_status("Preparing AI reply...")
        task.start()

    def _assisted_reply_ready(self, result):
        self.inbox_widget.refresh()
        self.approval_widget.refresh(preserve_editor=True)
        if result.draft_id and not self._background_drafting:
            self._review_new_draft(result.draft_id)
            self.approval_widget.status_label.setText("Review the reply and its sources, save changes, then Approve reply.")
        elif result.error:
            self.inbox_widget._reply_status(result.error + " You can use Write reply instead.")
            self.approval_widget.status_label.setText(result.error)

    def _reply_jobs_finished(self):
        old, self.email_draft_task = self.email_draft_task, None
        old.deleteLater()
        self.inbox_widget._set_reply_controls(self.inbox_widget._rows.get(self.inbox_widget.selected_email_id))
        self.approval_widget.update_delivery_controls()
        if self.email_setup:
            self.email_setup.refresh()

    def _open_automation_email(self, email_id, customer_id):
        if customer_id is not None:
            self._open_assigned_message(customer_id, email_id)
        else:
            self.navigate(0)
            self.inbox_widget.open_message(email_id)

    def _apply_gmail_connection(self, config, provider):
        if self.demo_mode:
            return
        if self.use_email_workflow and (self.config.test_mailbox, self.config.send_allowlist, self.config.gmail_write_enabled, self.config.enable_sending) != (config.test_mailbox, config.send_allowlist, config.gmail_write_enabled, config.enable_sending):
            from app.db.database import open_database
            with open_database(self.database_path) as connection:
                connection.execute("DELETE FROM desktop_reply_approvals")
        self.config = config
        self.inbox_widget.provider = provider
        self.inbox_widget.allowed_senders = config.gmail_allowed_senders
        self.inbox_widget.update_import_scope()
        self.inbox_widget.import_button.setText("Import from Gmail")
        self.inbox_widget.cleanup_action.setVisible(True)
        self.approval_widget.set_connection(config, provider)
        self._mail_pause_changed(self.settings_widget.gmail_widget.pause_checkbox.isChecked())
        self.inbox_widget.status_label.setText("Gmail connected. Click Import from Gmail to read incoming messages.")
        self._configure_automation_timer()
        if self._import_after_connection:
            self._import_after_connection = False
            self.navigate(0)
            QTimer.singleShot(0, self.inbox_widget.start_import)

    def _connect_and_import_gmail(self):
        if self.demo_mode:
            return
        self._import_after_connection = True
        self.navigate(5)
        control = self.settings_widget.gmail_widget
        control.status_label.setText("Connect Gmail below. After connecting, Inbox will open and import your emails automatically. Sending permissions can stay off.")
        control.account_input.setFocus()

    def _mail_pause_changed(self, paused):
        self.inbox_widget.import_button.setEnabled(not paused and self.inbox_widget.import_task is None)
        self.approval_widget.update_delivery_controls()
        self.safety_label.setText("Mail activity paused" if paused else
            ("Sending enabled for reviewed, allowed recipients" if self.config.sending_enabled and not self.demo_mode else "Sending is disabled"))

    def _change_gmail_import_options(self):
        if self.demo_mode:
            return
        self._import_after_connection = True
        self.navigate(5)
        control = self.settings_widget.gmail_widget
        control.status_label.setText("Import uses these sender and date limits. Leave Import senders empty for all senders, choose the lookback days, then Connect / reconnect Gmail to apply and import.")
        control.senders_input.setFocus()

    def closeEvent(self, event):
        tasks = [self.settings_widget.gmail_widget.task, self.approval_widget.delivery_task,
                 getattr(self.inbox_widget, "import_task", None), self.automation_widget.task,
                 self.files_widget.documents_widget.task]
        tasks.extend([self.inbox_widget.draft_task, self.faq_widget.test_task])
        tasks.extend([self.email_draft_task, self.email_setup.task if self.email_setup else None])
        if any(task is not None and task.isRunning() for task in tasks):
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Operation in progress", "Wait for the current operation to finish before closing. Document processing can stop after its current section.")
            event.ignore()
            return
        self.automation_timer.stop()
        if hasattr(self, 'workflow_heartbeat'):
            self.workflow_heartbeat.stop()
        self.files_widget.documents_widget.prepare_to_close()
        super().closeEvent(event)

    def navigate(self, index: int) -> None:
        if self.use_email_workflow and index in (1, 3, 4, 6):
            self.more_tools_button.setChecked(True)
        self.navigation.setCurrentRow(index)

    def _show_more_tools(self, shown):
        for index in (1, 3, 4, 6):
            self.navigation.item(index).setHidden(not shown)

    def _open_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        self.page_title.setText(self.page_names[index])
        self.help_label.setText(self.page_help[index])
        self.customer_context.setVisible(index in (0, 4))
        self.example_answers_button.setVisible(index == 3 and self._examples_available)
        if index == 2:
            self.approval_widget.refresh()
            self._update_review_count()
        elif index == 3:
            self.faq_widget.refresh()
        elif index == 4:
            self.files_widget.refresh()
        elif index == 6:
            self.automation_widget.refresh_history()
        elif index == 7 and self.replied_widget is not None:
            self._refresh_reply_history()

    def _reload_customers(self) -> None:
        blocker = QSignalBlocker(self.customer_selector)
        self.customer_selector.clear()
        self.customer_selector.addItem("Choose a customer…", None)
        for customer in list_customers(self.database_path):
            self.customer_selector.addItem(f"{customer.name} · {customer.email}", customer.id)
        index = self.customer_selector.findData(self.customers_widget.selected_customer_id)
        self.customer_selector.setCurrentIndex(max(0, index))
        del blocker

    def _choose_customer(self, index: int) -> None:
        customer_id = self.customer_selector.currentData()
        if customer_id is None:
            self.customers_widget.clear_form()
        else:
            self.customers_widget.select_customer_id(int(customer_id))

    def _customer_changed(self, customer_id) -> None:
        blocker = QSignalBlocker(self.customer_selector)
        self.customer_selector.setCurrentIndex(max(0, self.customer_selector.findData(customer_id)))
        del blocker

    def _review_new_draft(self, draft_id: int) -> None:
        self.navigate(2)
        self.approval_widget.select_draft(draft_id)

    def _open_assigned_message(self, customer_id: int, email_id: int) -> None:
        self.customers_widget.select_customer_id(customer_id)
        self.inbox_widget.open_message(email_id, customer_id)
        self.navigate(0)

    def _update_unread_count(self, count):
        self.navigation.item(0).setText(f"Inbox ({count} unread)" if count else "Inbox")

    def _update_review_count(self) -> None:
        count = len(self.approval_widget._drafts)
        self.navigation.item(2).setText(f"Review replies ({count})" if count else "Review replies")
        self._refresh_reply_history()

    def _refresh_reply_history(self):
        if self.replied_widget is not None:
            from app.db.replied import count
            total = count(self.database_path)
            self.navigation.item(7).setText(f'Replied ({total})' if total else 'Replied')
            self.replied_widget.refresh()

    def _load_example_answers(self) -> None:
        self.inbox_widget.load_sample_faqs()
        self.faq_widget.refresh()

    def _apply_style(self) -> None:
        self.setStyleSheet("""
            QMainWindow, QWidget { font-family: 'Segoe UI'; font-size: 13px; color: #192638; }
            QMainWindow { background: #f5f7fa; }
            QFrame#sidebar { background: #ffffff; border-right: 1px solid #dce2eb; }
            QLabel#brand { font-size: 23px; font-weight: 700; }
            QLabel#pageTitle { font-size: 25px; font-weight: 600; }
            QLabel#mutedLabel { color: #5c6b80; }
            QListWidget#navigation { background: transparent; border: none; outline: none; }
            QListWidget#navigation::item { padding: 12px 10px; margin: 3px 0; border-radius: 6px; }
            QListWidget#navigation::item:selected { color: #164ca8; background: #e8efff; font-weight: 600; }
            QLineEdit, QComboBox, QTextEdit, QPlainTextEdit, QTableWidget, QListWidget {
                background: #ffffff; color: #192638; border: 1px solid #ccd5e1; border-radius: 4px;
                selection-background-color: #dae7ff; selection-color: #192638;
            }
            QLineEdit, QComboBox { padding: 6px; }
            QPushButton, QToolButton { padding: 7px 12px; border: 1px solid #ccd5e1; border-radius: 5px; background: #ffffff; }
            QPushButton:hover, QToolButton:hover { background: #edf2fa; }
            QPushButton#primaryButton { color: #ffffff; background: #245bce; border-color: #245bce; font-weight: 600; }
            QPushButton:disabled { color: #7f8b9b; background: #e9edf3; border-color: #dce2eb; }
            QGroupBox { border: none; margin-top: 16px; padding-top: 10px; }
            QGroupBox#inboxGroup { margin-top: 0; padding-top: 0; }
            QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; font-weight: 600; }
            QHeaderView::section { background: #eef2f8; padding: 7px; border: none; font-weight: 600; }
            QTabBar::tab { padding: 8px 12px; }
            QSplitter::handle { background: #e3e8f0; }
        """)
