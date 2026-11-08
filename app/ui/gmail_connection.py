"""Visible Gmail connection and owner-controlled delivery settings."""
from hashlib import sha256
from PySide6.QtCore import Signal, QSignalBlocker
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)
from app.config import load_config, ConfigurationError, RunMode
from app.db.connections import save_connection, mail_paused, set_mail_paused
from app.providers.factory import email_provider
from .background import BackgroundTask


class GmailConnectionWidget(QWidget):
    connection_ready = Signal(object, object)
    paused_changed = Signal(bool)

    def __init__(self, database_path, config, demo_mode=False, parent=None):
        super().__init__(parent)
        self.database_path, self.config, self.demo_mode = database_path, config, demo_mode
        self.task = None
        self.account_input = QLineEdit(config.test_mailbox or "")
        self.account_input.setPlaceholderText("Your Gmail address")
        self.credentials_input = QLineEdit(config.google_credentials_file or "")
        self.credentials_input.setPlaceholderText("Google OAuth client JSON outside this project")
        self.browse_button = QPushButton("Choose file")
        self.browse_button.clicked.connect(self.choose_credentials)
        credential_row = QHBoxLayout()
        credential_row.addWidget(self.credentials_input, 1)
        credential_row.addWidget(self.browse_button)
        self.senders_input = QLineEdit(", ".join(config.gmail_allowed_senders))
        self.senders_input.setPlaceholderText("Optional sender addresses, separated by commas")
        self.limit_input = QSpinBox()
        self.limit_input.setRange(1, 500)
        self.limit_input.setValue(min(500, config.gmail_max_messages))
        self.days_input = QSpinBox()
        self.days_input.setRange(1, 3650)
        self.days_input.setValue(config.gmail_newer_than_days or 30)
        self.write_checkbox = QCheckBox("Allow creating Gmail drafts (extra Google permission)")
        self.write_checkbox.setChecked(config.gmail_write_enabled)
        self.send_checkbox = QCheckBox("Enable live sending after I review and confirm each reply")
        self.send_checkbox.setChecked(config.sending_enabled)
        self.send_checkbox.toggled.connect(lambda checked: self.write_checkbox.setChecked(True) if checked else None)
        self.recipients_input = QLineEdit(", ".join(config.send_allowlist))
        self.recipients_input.setPlaceholderText("Allowed recipients; leave blank to allow only your own account")
        self.connect_button = QPushButton("Connect / reconnect Gmail")
        self.connect_button.setObjectName("primaryButton")
        self.connect_button.clicked.connect(self.connect_gmail)
        self.pause_checkbox = QCheckBox("Pause mail imports and delivery")
        self.pause_checkbox.setChecked(mail_paused(database_path))
        self.pause_checkbox.toggled.connect(self.change_pause)
        self.status_label = QLabel("Connect to verify your Gmail account. Google sign-in opens only when needed.")
        self.status_label.setWordWrap(True)
        form = QFormLayout()
        form.addRow("Gmail account", self.account_input)
        form.addRow("OAuth client file", credential_row)
        form.addRow("Import senders", self.senders_input)
        options = QHBoxLayout()
        options.addWidget(self.limit_input)
        options.addWidget(QLabel("messages; last"))
        options.addWidget(self.days_input)
        options.addWidget(QLabel("days"))
        options.addStretch()
        form.addRow("Import window", options)
        form.addRow(self.write_checkbox)
        form.addRow(self.send_checkbox)
        form.addRow("Send only to", self.recipients_input)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Gmail connection")
        title.setStyleSheet("font-size: 17px; font-weight:600")
        layout.addWidget(title)
        layout.addLayout(form)
        layout.addWidget(self.connect_button)
        layout.addWidget(self.pause_checkbox)
        layout.addWidget(self.status_label)
        if demo_mode:
            self.setEnabled(False)
            self.status_label.setText("Demo stays offline. Open CRM.cmd to connect a real Gmail account.")

    def choose_credentials(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose Google OAuth client file", "", "JSON files (*.json)")
        if path:
            self.credentials_input.setText(path)

    def connection_values(self):
        account = self.account_input.text().strip().lower()
        import re
        addresses = [account] + [value.strip().lower() for text in (self.senders_input.text(), self.recipients_input.text()) for value in text.split(",") if value.strip()]
        if any(not re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}", value) for value in addresses):
            raise ValueError("Enter valid individual email addresses, separated by commas.")
        if self.send_checkbox.isChecked() and not self.write_checkbox.isChecked():
            raise ValueError("Gmail draft permissions are required before enabling sending.")
        user = self.config.google_keyring_user if account == self.config.test_mailbox else "account_" + sha256(account.encode()).hexdigest()[:24]
        return {"MINI_CRM_MODE": "Live" if self.send_checkbox.isChecked() else "Dry-run", "MINI_CRM_PROVIDER": "gmail",
            "MINI_CRM_TEST_MAILBOX": account, "MINI_CRM_GOOGLE_CREDENTIALS_FILE": self.credentials_input.text(),
            "MINI_CRM_GOOGLE_KEYRING_SERVICE": self.config.google_keyring_service, "MINI_CRM_GOOGLE_KEYRING_USER": user,
            "MINI_CRM_GMAIL_ALLOWED_SENDERS": self.senders_input.text(), "MINI_CRM_GMAIL_MAX_MESSAGES": str(self.limit_input.value()),
            "MINI_CRM_GMAIL_NEWER_THAN_DAYS": str(self.days_input.value()),
            "MINI_CRM_GMAIL_WRITE_ENABLED": str(self.write_checkbox.isChecked()).lower(),
            "MINI_CRM_ENABLE_SENDING": str(self.send_checkbox.isChecked()).lower(),
            "MINI_CRM_SEND_ALLOWLIST": self.recipients_input.text().strip() or account}

    def connect_gmail(self):
        if self.demo_mode or self.task is not None:
            return
        try:
            values = self.connection_values()
            config = load_config(values)
            provider = email_provider(config)
        except (ConfigurationError, ValueError) as error:
            self.status_label.setText(str(error))
            return
        def operation():
            provider.verify_connection()
            save_connection(self.database_path, values)
            return config, provider
        self.status_label.setText("Connecting to Gmail… Complete Google sign-in if a browser opens.")
        self.connect_button.setEnabled(False)
        self.task = BackgroundTask(operation, self)
        self.task.succeeded.connect(self.connected)
        self.task.failed.connect(self.status_label.setText)
        self.task.finished.connect(self.finished)
        self.task.start()

    def connected(self, result):
        self.config, provider = result
        self.status_label.setText("Gmail connected. " + ("Reviewed sending is enabled for allowed recipients." if self.config.sending_enabled else "Import is ready. Live sending is disabled."))
        self.connection_ready.emit(self.config, provider)

    def finished(self):
        old, self.task = self.task, None
        if old:
            old.deleteLater()
        self.connect_button.setEnabled(True)

    def change_pause(self, paused):
        if self.demo_mode:
            return
        try:
            set_mail_paused(self.database_path, paused)
        except Exception:
            blocker = QSignalBlocker(self.pause_checkbox)
            self.pause_checkbox.setChecked(not paused)
            del blocker
            self.status_label.setText("Could not change pause state yet. Wait for the current mail operation.")
            return
        self.paused_changed.emit(paused)
        self.status_label.setText("Mail activity paused." if paused else "Mail activity resumed.")
