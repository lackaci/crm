"""Shared incoming queue, indexed filters and a side-by-side reading pane."""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QSignalBlocker, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QGroupBox, QHBoxLayout, QMessageBox,
    QPushButton, QLabel, QTableWidget, QTableWidgetItem, QTabWidget, QPlainTextEdit,
    QComboBox, QHeaderView, QLineEdit, QMenu, QSplitter, QToolButton, QVBoxLayout, QWidget)

from app.db import inbox as inbox_store
from app.db.emails import (get_email, mark_email_seen, remove_gmail_emails_not_from,
                           assign_email_to_customer)
from app.db.customers import list_customers
from app.db.drafts import list_drafts
from app.db.faqs import create_faq, list_approved_faqs
from app.email_sync import synchronize_email
from app.providers.llm_fake import FakeLLMProvider
from app.providers.email_mock import MockEmailProvider
from app.providers.factory import BackgroundGmailImport
from app.reply.pipeline import process_email
from .background import BackgroundTask


class CustomerInboxWidget(QGroupBox):
    status_changed = Signal()
    draft_created = Signal(int)
    assisted_reply_requested = Signal(int)
    email_assigned = Signal(int, int)
    imported = Signal()
    unread_changed = Signal(int)
    gmail_setup_requested = Signal()
    saved_answers_requested = Signal()
    import_options_requested = Signal()
    headers = ("Subject", "Sender", "Preview", "Status", "Assigned to", "Received", "Incoming messages")
    filter_labels = {"all": "All", "unread": "Unread", "assignment": "Needs assignment",
                     "attention": "Needs attention", "pending": "Reply pending"}

    def __init__(self, database_path, provider=None, llm_provider=None, allowed_senders=(),
                 parent=None, storage_root=None):
        super().__init__("", parent)
        self.database_path, self.storage_root = database_path, storage_root
        self.provider = provider or MockEmailProvider()
        self.llm_provider = llm_provider or FakeLLMProvider()
        self.allowed_senders = allowed_senders
        self.import_task = self.draft_task = None
        self._draft_email_id = None
        self.background_import = False
        self.demo_mode = False
        self.use_review_workflow = False
        self.customer_id = self.selected_email_id = self.selected_unmatched_id = None
        self.filter_name, self.offset = "all", 0
        self._messages, self._rows = [], {}
        self.setObjectName("inboxGroup")
        self.import_button = QPushButton("Import from Gmail")
        self.import_button.setObjectName("primaryButton")
        self.import_button.setToolTip("Check for new mail; already downloaded Gmail messages are skipped.")
        self.import_button.clicked.connect(self.request_gmail_import)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search sender, subject or message")
        self.search_input.setAccessibleName("Search messages")
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(180)
        self.search_timer.timeout.connect(self._search_changed)
        self.search_input.textChanged.connect(lambda: self.search_timer.start())
        self.scope_combo = QComboBox()
        self.scope_combo.addItems(["All customers", "Selected customer"])
        self.scope_combo.currentIndexChanged.connect(self._search_changed)
        self.more_button = QToolButton()
        self.more_button.setText("More")
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_menu = QMenu(self.more_button)
        self.more_menu.addAction("Refresh stored messages", self.refresh)
        self.more_menu.addAction("Import example emails (offline)", self.import_sample_emails)
        self.cleanup_action = self.more_menu.addAction("Clean up imported Gmail...", self.cleanup_other_gmail)
        self.cleanup_action.setVisible(getattr(self.provider, "provider_name", "mock") == "gmail")
        self.more_button.setMenu(self.more_menu)
        self.status_label = QLabel("Refresh to check for new mail. Select any message to read it.")
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.import_scope_panel = QWidget()
        scope_bar = QHBoxLayout(self.import_scope_panel)
        scope_bar.setContentsMargins(0, 0, 0, 0)
        self.import_scope_label = QLabel()
        self.import_scope_label.setWordWrap(True)
        self.import_scope_label.setTextFormat(Qt.TextFormat.PlainText)
        self.import_options_button = QToolButton()
        self.import_options_button.setText("Change import options")
        self.import_options_button.clicked.connect(lambda: self.import_options_requested.emit())
        scope_bar.addWidget(self.import_scope_label, 1)
        scope_bar.addWidget(self.import_options_button)
        self.unread_label = QLabel()
        self.filter_buttons = {}
        filter_bar = QHBoxLayout()
        for name, label in self.filter_labels.items():
            button = QPushButton(label)
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, name=name: self.choose_filter(name))
            self.filter_buttons[name] = button
            filter_bar.addWidget(button)
        self.email_table, self.unmatched_table = self._table(), self._table()
        self.inbox_tabs = QTabWidget()
        self.inbox_tabs.addTab(self.email_table, "Incoming messages")
        self.inbox_tabs.addTab(self.unmatched_table, "Needs assignment")
        self.inbox_tabs.tabBar().hide()  # The quick filters are the single visible navigation.
        self.inbox_tabs.currentChanged.connect(self._change_view)
        self.email_table.itemSelectionChanged.connect(self._select_email)
        self.unmatched_table.itemSelectionChanged.connect(self._select_unmatched_email)
        self.email_preview = QPlainTextEdit()
        self.email_preview.setReadOnly(True)
        self.email_preview.setPlaceholderText("Select a message to read it here.")
        self.message_details = QLabel("No message selected")
        self.message_details.setWordWrap(True)
        self.message_details.setTextFormat(Qt.TextFormat.PlainText)
        self.customer_combo = QComboBox()
        self.customer_combo.setMinimumContentsLength(12)
        self.assign_button = QPushButton("Link to customer")
        self.assign_button.clicked.connect(self.assign_selected_email)
        self.assignment_panel = QWidget()
        assignment = QVBoxLayout(self.assignment_panel)
        assignment.setContentsMargins(0, 0, 0, 0)
        assignment.addWidget(QLabel("Link this message to:"))
        assignment.addWidget(self.customer_combo)
        assignment.addWidget(self.assign_button)
        self.create_draft_button = QPushButton("Prepare reply")
        self.create_draft_button.setObjectName("primaryButton")
        self.create_draft_button.setEnabled(False)
        self.create_draft_button.clicked.connect(self.create_draft)
        self.reply_status_label = QLabel()
        self.reply_status_label.setWordWrap(True)
        self.reply_status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.saved_answers_button = QPushButton("Open saved answers")
        self.saved_answers_button.clicked.connect(lambda: self.saved_answers_requested.emit())
        self.saved_answers_button.hide()
        reader = QWidget()
        reading = QVBoxLayout(reader)
        reading.setContentsMargins(6, 0, 0, 0)
        reading.addWidget(self.message_details)
        reading.addWidget(self.email_preview, 1)
        reading.addWidget(self.assignment_panel)
        reading.addWidget(self.reply_status_label)
        reading.addWidget(self.saved_answers_button)
        reading.addWidget(self.create_draft_button)
        self.write_reply_button = QPushButton("Write reply / review existing")
        self.write_reply_button.clicked.connect(self.write_reply)
        reading.addWidget(self.write_reply_button)
        self.message_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.message_splitter.addWidget(self.inbox_tabs)
        self.message_splitter.addWidget(reader)
        self.message_splitter.setStretchFactor(0, 3)
        self.message_splitter.setStretchFactor(1, 2)
        self.message_splitter.setSizes([560, 330])
        self.page_label = QLabel()
        self.previous_button, self.next_button = QPushButton("Previous"), QPushButton("Next")
        self.previous_button.clicked.connect(lambda: self.change_page(-1))
        self.next_button.clicked.connect(lambda: self.change_page(1))
        page_bar = QHBoxLayout()
        page_bar.addWidget(self.page_label, 1)
        page_bar.addWidget(self.previous_button)
        page_bar.addWidget(self.next_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        actions = QHBoxLayout()
        for control in (self.import_button, self.search_input, self.scope_combo, self.more_button):
            actions.addWidget(control, 1 if control is self.search_input else 0)
        layout.addLayout(actions)
        layout.addWidget(self.import_scope_panel)
        self.workflow_summary = QLabel()
        self.workflow_summary.setWordWrap(True)
        workflow_bar = QHBoxLayout()
        workflow_bar.addWidget(self.workflow_summary, 1)
        self.pause_checks_button = QPushButton('Pause checks')
        self.pause_checks_button.hide()
        workflow_bar.addWidget(self.pause_checks_button)
        layout.addLayout(workflow_bar)
        layout.addLayout(filter_bar)
        layout.addWidget(self.unread_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.message_splitter, 1)
        layout.addLayout(page_bar)
        # Legacy convenience controls remain callable by existing integrations.
        self.show_unmatched_button = QPushButton("Refresh unassigned", self)
        self.show_unmatched_button.clicked.connect(self.refresh_unmatched)
        self.cleanup_button = QPushButton("Cleanup", self)
        self.cleanup_button.clicked.connect(self.cleanup_other_gmail)
        self.load_faqs_button = QPushButton("Load approved sample FAQs", self)
        self.load_faqs_button.clicked.connect(self.load_sample_faqs)
        for button in (self.show_unmatched_button, self.cleanup_button, self.load_faqs_button):
            button.hide()
        self.refresh_unmatched()
        self.refresh()
        self.update_import_scope()

    def _table(self):
        table = QTableWidget(0, len(self.headers))
        table.setHorizontalHeaderLabels(self.headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setWordWrap(False)
        table.setAlternatingRowColors(True)
        table.verticalHeader().hide()
        table.setMinimumWidth(290)
        # One compact entry keeps sender, preview, date and badges visible
        # beside the reader, including at the minimum window size. Structured
        # columns remain available to related UI integrations.
        for column in range(6):
            table.setColumnHidden(column, True)
        table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().hide()
        return table

    def _scope(self):
        return self.customer_id if self.scope_combo.currentIndex() == 1 else None

    def _search_changed(self):
        self.offset = 0
        self.refresh()

    def choose_filter(self, name):
        self.filter_name, self.offset = name, 0
        blocker = QSignalBlocker(self.inbox_tabs)
        self.inbox_tabs.setCurrentIndex(1 if name == "assignment" else 0)
        del blocker
        self._clear_reader()
        self.refresh()

    def _change_view(self, index):
        self.filter_name, self.offset = ("assignment" if index == 1 else "all"), 0
        self._clear_reader()
        self.refresh()

    def _clear_reader(self):
        self.selected_email_id = self.selected_unmatched_id = None
        self.email_preview.clear()
        self.reply_status_label.clear()
        self.saved_answers_button.hide()
        self.message_details.setText("No message selected")
        self.assignment_panel.hide()
        self.assign_button.setEnabled(False)
        self.create_draft_button.setEnabled(False)
        self.create_draft_button.setText("Prepare reply")
        self.write_reply_button.setEnabled(False)
        for table in (self.email_table, self.unmatched_table):
            blocker = QSignalBlocker(table)
            table.clearSelection()
            del blocker

    def set_customer(self, customer_id):
        changed = self.customer_id != customer_id
        self.customer_id = customer_id
        # An explicit customer change scopes the queue; startup restores All.
        if changed:
            blocker = QSignalBlocker(self.scope_combo)
            self.scope_combo.setCurrentIndex(1 if customer_id is not None else 0)
            del blocker
            self.offset = 0
            self._clear_reader()
        if customer_id is not None:
            self.customer_combo.setCurrentIndex(max(0, self.customer_combo.findData(customer_id)))
        self.refresh()

    @staticmethod
    def _badges(row):
        if row.get('reply_sent'):
            return 'Sent'
        badges = ["Sent" if row.get('reply_sent') else "New / unread" if row["status"] == "new" else row["status"].title()]
        if row["customer_id"] is None:
            badges.append("Needs assignment")
        if row["reply_pending"]:
            badges.append("Reply pending")
        if row.get('ai_status') in {'queued', 'generating', 'failed'}:
            badges.append({'queued': 'AI queued', 'generating': 'Preparing reply', 'failed': 'AI failed'}[row['ai_status']])
        if row.get('reply_approved'):
            badges.append('Approved / ready to send')
        elif row.get('ai_status') == 'ready' and row['reply_pending']:
            badges.append('Ready for review')
        if row["attention"]:
            badges.append("Needs attention")
        return " | ".join(badges)

    def _render(self, table, rows, previous):
        blocker = QSignalBlocker(table)
        table.clearSelection()
        table.setCurrentCell(-1, -1)
        table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            self._rows[row["id"]] = row
            preview = " ".join(row["body"].split())[:160]
            date = datetime.fromtimestamp(row["received_ts"]).astimezone().strftime("%d %b %Y %H:%M") if row["received_ts"] else "Date unavailable"
            values = (row["subject"], f"{row['from_name']} <{row['from_email']}>", preview,
                      self._badges(row), row["staff_name"] or "Unassigned", date, self._summary(row))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, row["id"])
                item.setToolTip(value)
                if row["status"] == "new":
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                if column in (3, 6):
                    item.setForeground(QColor("#9b3c10" if row["attention"] else "#164ca8"))
                table.setItem(index, column, item)
            table.setRowHeight(index, 94)
            if row["id"] == previous:
                table.selectRow(index)
                table.scrollToItem(table.item(index, 6))
        del blocker

    def _summary(self, row):
        date = datetime.fromtimestamp(row["received_ts"]).astimezone().strftime("%d %b %Y %H:%M") if row["received_ts"] else "Date unavailable"
        preview = " ".join(row["body"].split())[:100]
        badges = self._badges(row).replace("New / unread", "Unread")
        return f"{row['from_name']} - {row['subject']}\n{preview}\n{date}\n{badges}"

    def _update_counts(self):
        totals = inbox_store.counts(self.database_path)
        scoped = inbox_store.counts(self.database_path, self.search_input.text(), self._scope())
        # Assignment is always a shared queue, independent of customer scope.
        scoped["assignment"] = inbox_store.counts(self.database_path, self.search_input.text())["assignment"]
        for name, button in self.filter_buttons.items():
            button.setText(f"{self.filter_labels[name]} ({scoped[name]})")
            button.setChecked(name == self.filter_name)
        self.unread_label.setText(f"Unread: {totals['unread']} across all incoming messages")
        self.unread_changed.emit(totals["unread"])
        self.inbox_tabs.setTabText(1, f"Needs assignment ({scoped['assignment']})")
        return scoped

    def refresh(self):
        previous = self.selected_unmatched_id if self.inbox_tabs.currentIndex() == 1 else self.selected_email_id
        self._rows = {}
        search = self.search_input.text()
        main_filter = "all" if self.filter_name == "assignment" else self.filter_name
        main_rows = inbox_store.messages(self.database_path, filter_name=main_filter, search=search,
                                        customer_id=self._scope(), offset=self.offset if self.inbox_tabs.currentIndex() == 0 else 0)
        unmatched = inbox_store.messages(self.database_path, filter_name="assignment", search=search,
                                        offset=self.offset if self.inbox_tabs.currentIndex() == 1 else 0)
        self._render(self.email_table, main_rows, previous if self.inbox_tabs.currentIndex() == 0 else None)
        self._render(self.unmatched_table, unmatched, previous if self.inbox_tabs.currentIndex() == 1 else None)
        counts = self._update_counts()
        active = unmatched if self.inbox_tabs.currentIndex() == 1 else main_rows
        total = counts[self.filter_name]
        if self.offset and self.offset >= total:
            self.offset = max(0, (total - 1) // 100 * 100)
            self.refresh()
            return
        self.page_label.setText(f"{self.offset + 1 if active else 0}-{self.offset + len(active)} of {total} messages; newest first")
        self.previous_button.setEnabled(self.offset > 0)
        self.next_button.setEnabled(self.offset + len(active) < total)
        if previous not in {row["id"] for row in active}:
            self._clear_reader()
        else:
            self.selected_email_id = previous if self.inbox_tabs.currentIndex() == 0 else None
            self.selected_unmatched_id = previous if self.inbox_tabs.currentIndex() == 1 else None
            self._read(previous, mark=False)

    def refresh_unmatched(self):
        selected = self.customer_combo.currentData()
        blocker = QSignalBlocker(self.customer_combo)
        self.customer_combo.clear()
        for customer in list_customers(self.database_path):
            self.customer_combo.addItem(f"{customer.name} <{customer.email}>", customer.id)
        self.customer_combo.setCurrentIndex(max(0, self.customer_combo.findData(selected)))
        del blocker
        self.refresh()

    def change_page(self, direction):
        self.offset = max(0, self.offset + direction * 100)
        self._clear_reader()
        self.refresh()

    def _select_email(self):
        self._select(self.email_table, unmatched=False)

    def _select_unmatched_email(self):
        self._select(self.unmatched_table, unmatched=True)

    def _select(self, table, unmatched):
        if (self.inbox_tabs.currentIndex() == 1) != unmatched:
            return
        items = table.selectedItems()
        if not items:
            self.selected_email_id = self.selected_unmatched_id = None
            self.create_draft_button.setEnabled(False)
            self.assignment_panel.hide()
            self.email_preview.clear()
            return
        email_id = int(items[0].data(Qt.ItemDataRole.UserRole))
        self.selected_email_id = None if unmatched else email_id
        self.selected_unmatched_id = email_id if unmatched else None
        self._read(email_id)
        row = self._rows[email_id]
        table.scrollToItem(table.item(items[0].row(), 6))
        table.item(items[0].row(), 3).setText(self._badges(row))
        table.item(items[0].row(), 6).setText(self._summary(row))
        for column in range(table.columnCount()):
            font = table.item(items[0].row(), column).font()
            font.setBold(row["status"] == "new")
            table.item(items[0].row(), column).setFont(font)

    def _read(self, email_id, mark=True):
        row = self._rows.get(email_id)
        if not row:
            return
        if mark:
            self.reply_status_label.clear()
            self.saved_answers_button.hide()
        if mark and row["status"] == "new":
            mark_email_seen(self.database_path, email_id)
            row["status"] = "seen"
            self._update_counts()
            self.status_changed.emit()
        self.message_details.setText("Customer: " + (row["customer_name"] or "Needs assignment") +
            "\nAssigned to: " + (row["staff_name"] or "Unassigned") + "\n" + self._badges(row) +
            ("\nAttention: " + (row["attention_reason"] or row["triage_reason"] or "Review this message personally.")[:180] if row["attention"] else ""))
        self.email_preview.setPlainText(f"From: {row['from_name']} <{row['from_email']}>\nSubject: {row['subject']}\nReceived: {row['received_at'] or 'Unavailable'}\n\n{row['body']}")
        self.assignment_panel.setVisible(row["customer_id"] is None)
        self.assign_button.setEnabled(row["customer_id"] is None and self.customer_combo.count() > 0)
        self._set_reply_controls(row)

    def _set_reply_controls(self, row):
        actionable = bool(row) and row['status'] != 'done' and not row.get('reply_sent')
        self.create_draft_button.setEnabled(actionable and self.draft_task is None)
        self.write_reply_button.setEnabled(actionable)
        from app.db import email_workflow as workflow
        assisted = self.use_review_workflow and (self.demo_mode or workflow.settings(self.database_path)['selected'])
        self.create_draft_button.setText("Link customer first" if not assisted and row and row["customer_id"] is None else
            "Review reply" if row and row["reply_pending"] else "Prepare reply")

    def _reply_status(self, text):
        self.status_label.setText(text)
        self.reply_status_label.setText(text)

    def assign_selected_email(self):
        email_id = self.selected_unmatched_id or self.selected_email_id
        customer_id = self.customer_combo.currentData()
        if email_id is None or customer_id is None:
            return
        try:
            assign_email_to_customer(self.database_path, email_id, int(customer_id))
        except (ValueError, sqlite3.Error):
            self.status_label.setText("This message could not be linked. Refresh and select an existing customer.")
            return
        self.status_changed.emit()
        self.open_message(email_id, customer_id)
        self.email_assigned.emit(int(customer_id), email_id)
        self.status_label.setText("Email assigned to the selected customer.")

    def open_message(self, email_id, customer_id=None):
        self.search_timer.stop()
        blocker = QSignalBlocker(self.search_input)
        self.search_input.clear()
        del blocker
        if customer_id is not None:
            self.customer_id = customer_id
        blocker = QSignalBlocker(self.scope_combo)
        self.scope_combo.setCurrentIndex(1 if customer_id is not None else 0)
        del blocker
        self.choose_filter("all")
        # Direct history/review navigation works even beyond the first page.
        while True:
            for row in range(self.email_table.rowCount()):
                if self.email_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == email_id:
                    self.email_table.selectRow(row)
                    return
            if not self.next_button.isEnabled():
                return
            self.offset += 100
            self.refresh()

    def import_sample_emails(self):
        self.start_import()

    def set_demo_mode(self, enabled):
        self.demo_mode = enabled
        self.import_button.setText("Import example emails" if enabled else "Import from Gmail")
        self.update_import_scope()

    def update_import_scope(self):
        connected = getattr(self.provider, "provider_name", "mock") == "gmail" and not self.demo_mode
        self.import_scope_panel.setVisible(connected)
        if connected:
            senders = len(getattr(self.provider, "allowed_senders", ()))
            days = getattr(self.provider, "newer_than_days", None)
            limit = getattr(self.provider, "max_messages", 100)
            sender_scope = f"{senders} allowed sender" + ("s" if senders != 1 else "") if senders else "All senders"
            date_scope = f"Last {days} days" if days else "Any date"
            self.import_scope_label.setText(f"Gmail import: {sender_scope} | {date_scope} | Up to {limit} per import")

    def request_gmail_import(self):
        if self.demo_mode:
            self.start_import()
        elif getattr(self.provider, "provider_name", "mock") == "gmail":
            self.start_import()
        else:
            self.status_label.setText("Connect Gmail to import your messages. Import will start after you connect.")
            self.gmail_setup_requested.emit()

    def start_import(self, automatic=False):
        from app.db.connections import mail_paused
        if self.import_task is not None:
            return
        if mail_paused(self.database_path):
            self.status_label.setText("Mail activity is paused. Resume it in Gmail connection.")
            return
        self.background_import = automatic
        if getattr(self.provider, "provider_name", "mock") == "gmail":
            provider = BackgroundGmailImport(self.provider) if automatic else self.provider
            task = BackgroundTask(lambda: synchronize_email(self.database_path, provider, storage_root=self.storage_root,
                llm_provider=self.llm_provider, progress=task.progress.emit), self)
            self.import_task = task
            task.progress.connect(self.status_label.setText)
            task.succeeded.connect(self._import_complete)
            task.failed.connect(self._import_failed)
            task.finished.connect(self._import_finished)
            self.import_button.setEnabled(False)
            self.import_button.setText("Importing from Gmail...")
            self.status_label.setText("Connecting to Gmail...")
            task.start()
        else:
            self._import_complete(synchronize_email(self.database_path, self.provider, storage_root=self.storage_root, llm_provider=self.llm_provider))

    def _import_failed(self, message):
        self.refresh()
        self.status_label.setText("Refresh could not finish. Check Gmail connection and try again; saved messages are safe.")

    def _import_finished(self):
        old, self.import_task = self.import_task, None
        old.deleteLater()
        from app.db.connections import mail_paused
        self.import_button.setEnabled(not mail_paused(self.database_path))
        self.import_button.setText("Import from Gmail")

    def _import_complete(self, result):
        self.last_import_result = result
        # A partial failure must still show already-committed messages.
        if not self.background_import:
            self.search_timer.stop()
            search_blocker = QSignalBlocker(self.search_input)
            self.search_input.clear()
            del search_blocker
            blocker = QSignalBlocker(self.scope_combo)
            self.scope_combo.setCurrentIndex(0)
            del blocker
            self.choose_filter("all")
        self.refresh_unmatched()
        text = f"Imported {result.imported} new, {result.existing} already existed, {result.failed} failed."
        if result.remaining:
            text += f" {result.remaining} queued; Refresh or wait for the next scheduled check."
        if result.error:
            text += " " + result.error
        elif not result.imported and not result.remaining:
            text += " No new emails matched your import settings. Already imported mail is shown below; use Change import options to adjust sender or date limits."
        if result.automation is not None:
            text += f" {result.automation.drafts} reply suggestion(s); {result.automation.attention} need attention."
        if result.automation_error:
            text += " Automation could not finish; unprocessed mail will be retried."
        if getattr(result.automation, 'error', None):
            text += " " + result.automation.error
        from app.db import email_workflow as workflow
        if workflow.settings(self.database_path)['selected']:
            import time
            workflow.update(self.database_path, last_check=time.time(), last_result=text)
        self.status_label.setText(text)
        self.status_changed.emit()
        self.imported.emit()

    def create_draft(self):
        if self.use_review_workflow:
            from app.db import email_workflow as workflow
            if self.demo_mode or workflow.settings(self.database_path)["selected"]:
                selected = self.selected_email_id or self.selected_unmatched_id
                if selected is None:
                    self._reply_status("Select a message first.")
                    return
                message = get_email(self.database_path, selected)
                if message is None or message.status == 'done':
                    self._reply_status('This message is already completed. Sent replies are in the Replied menu.')
                    return
                existing = [d for d in list_drafts(self.database_path, selected) if d.status == 'pending']
                if existing:
                    self.draft_created.emit(existing[-1].id)
                else:
                    self.assisted_reply_requested.emit(selected)
                return
        if self.draft_task is not None:
            self._reply_status("Preparing your reply. Please wait for the current request to finish.")
            return
        selected_id = self.selected_email_id or self.selected_unmatched_id
        if selected_id is None:
            self._reply_status("Select an email in the list first, then click Prepare reply.")
            return
        try:
            message = get_email(self.database_path, selected_id)
            pending = [draft for draft in list_drafts(self.database_path, selected_id) if draft.status == "pending"]
            approved_answers = list_approved_faqs(self.database_path)
        except sqlite3.Error:
            self._reply_status("Could not read this message. Import or refresh the stored messages, then try again.")
            return
        if message is None:
            self._reply_status("This email is no longer available. Import your messages again.")
            return
        if message.customer_id is None:
            self.assignment_panel.show()
            self._reply_status("Choose a customer in this pane, then click Link to customer. You can prepare the reply after linking."
                               if self.customer_combo.count() else "Add a customer in Customers first, then return here and link this email.")
            self.customer_combo.setFocus()
            return
        self._draft_email_id = message.id
        if pending:
            self._reply_status("Opening your existing reply in Review replies.")
            self.draft_created.emit(pending[-1].id)
            return
        if message.status == "done":
            self._reply_status("This message is already completed. Select an unfinished message to prepare a reply.")
            return
        if not approved_answers:
            self._reply_status("No approved saved answers are available. Open saved answers, add and approve an answer, then try again.")
            self.saved_answers_button.show()
            return
        if getattr(self.llm_provider, "uses_network", False):
            provider = self.llm_provider
            self.draft_task = BackgroundTask(lambda: process_email(message, self.database_path, provider), self)
            self.draft_task.succeeded.connect(self._draft_complete)
            self.draft_task.failed.connect(self._draft_failed)
            self.draft_task.finished.connect(self._draft_finished)
            self.create_draft_button.setEnabled(False)
            self._reply_status("Preparing a reply with the selected online model...")
            self.create_draft_button.setText("Preparing reply...")
            self.draft_task.start()
            return
        try:
            self._draft_complete(process_email(message, self.database_path, self.llm_provider))
        except (OSError, ValueError, sqlite3.Error, RuntimeError):
            self._draft_failed("Could not prepare this reply. Check the saved answers and try again.")

    def _draft_failed(self, message):
        self._reply_status("Could not prepare the reply. " + message)

    def write_reply(self):
        selected = self.selected_email_id or self.selected_unmatched_id
        if selected is None:
            self._reply_status("Select a message, then choose Write reply.")
            return
        try:
            from app.reply.email_assistant import manual_reply
            self.draft_created.emit(manual_reply(self.database_path, selected))
        except ValueError as error:
            self._reply_status(str(error))

    def _draft_complete(self, result):
        if result.status == "pending":
            self.refresh()
            self._reply_status("Reply prepared. Opening Review replies.")
            self.draft_created.emit(result.draft_id)
        else:
            inbox_store.mark_attention(self.database_path, self._draft_email_id, result.reason or "Manual review required")
            self.refresh()
            reason = result.reason or "Please review this message personally."
            if reason == "no sufficiently confident approved FAQ match":
                reason = "No approved saved answer matches this email confidently. Add or update an answer in Saved answers and approve it, then try again."
                self.saved_answers_button.show()
            self._reply_status("Needs your attention: " + reason)

    def _draft_finished(self):
        old, self.draft_task = self.draft_task, None
        if old:
            old.deleteLater()
        self._set_reply_controls(self._rows.get(self.selected_email_id or self.selected_unmatched_id))

    def cleanup_other_gmail(self) -> None:
        """Remove previously imported Gmail messages outside the allowlist."""
        if not self.allowed_senders:
            QMessageBox.warning(
                self, "No sender allowlist", "Add allowed Gmail senders in .env first."
            )
            return
        answer = QMessageBox.question(
            self,
            "Clean up other Gmail",
            "Remove previously imported Gmail messages from other senders?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed = remove_gmail_emails_not_from(
            self.database_path, self.allowed_senders
        )
        self.refresh()
        self.refresh_unmatched()
        self.status_label.setText(f"{removed} non-allowed Gmail email(s) removed.")

    def load_sample_faqs(self) -> None:
        """Load the local fixture as owner-approved FAQs for this prototype."""
        faq_path = Path(__file__).resolve().parents[2] / "sample_data" / "faqs.json"
        try:
            records = json.loads(faq_path.read_text(encoding="utf-8"))
            if not isinstance(records, list):
                raise ValueError("Sample FAQ data must be a JSON list.")
            existing_questions = {
                faq.question.casefold() for faq in list_approved_faqs(self.database_path)
            }
            loaded = 0
            for record in records:
                if not isinstance(record, dict):
                    raise ValueError("Sample FAQ records must be mappings.")
                question = record.get("question")
                answer = record.get("answer")
                if not isinstance(question, str) or not isinstance(answer, str):
                    raise ValueError("Each sample FAQ needs a question and answer.")
                if question.casefold() in existing_questions:
                    continue
                create_faq(
                    self.database_path,
                    question,
                    answer,
                    approved=True,
                    actor="sample-faq-loader",
                )
                existing_questions.add(question.casefold())
                loaded += 1
        except (OSError, json.JSONDecodeError, ValueError, sqlite3.Error) as error:
            QMessageBox.warning(self, "Could not load sample FAQs", str(error))
            return
        self.status_label.setText(f"{loaded} approved FAQ(s) loaded.")
