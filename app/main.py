"""Application entry point for the Mini CRM."""

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.db.database import initialize_database
from app.config import load_config
from app.providers.email_mock import MockEmailProvider
from app.providers.gmail import GmailProvider
from app.ui.main_window import MainWindow


def main() -> int:
    """Open the Mini CRM customer window."""
    application = QApplication(sys.argv)
    database_path = Path(__file__).resolve().parents[1] / "mini_crm.db"
    initialize_database(database_path)
    config = load_config()
    provider = (
        GmailProvider(
            config.google_credentials_file,
            keyring_service=config.google_keyring_service,
            keyring_user=config.google_keyring_user,
            expected_mailbox=config.test_mailbox,
            allowed_senders=config.gmail_allowed_senders,
            max_messages=config.gmail_max_messages,
            newer_than_days=config.gmail_newer_than_days,
        )
        if config.provider == "gmail"
        else MockEmailProvider()
    )
    window = MainWindow(database_path, email_provider=provider, config=config)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
