"""Application entry point for the Mini CRM."""

import sys
import argparse
import gc
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtWidgets import QApplication

from app.db.database import initialize_database
from app.config import AppConfig, RunMode, ConfigurationError, load_config
from app.db.connections import load_connection
from app.providers.email_mock import MockEmailProvider
from app.providers.gmail import GmailProvider
from app.ui.main_window import MainWindow


def main() -> int:
    """Open the Mini CRM customer window."""
    parser = argparse.ArgumentParser(description="Mini CRM desktop workspace")
    parser.add_argument("--demo", action="store_true", help="Open a temporary offline workspace with fictional data")
    args = parser.parse_args()
    application = QApplication.instance() or QApplication(sys.argv[:1])
    if args.demo:
        from app.demo import create_demo_workspace
        from app.providers.llm_fake import FakeLLMProvider
        with TemporaryDirectory(prefix="mini-crm-demo-") as folder:
            database_path, storage_root = create_demo_workspace(Path(folder))
            window = MainWindow(database_path, email_provider=MockEmailProvider(),
                llm_provider=FakeLLMProvider(), storage_root=storage_root,
                config=AppConfig(mode=RunMode.MOCK, provider="mock"), demo_mode=True)
            window.show()
            result = application.exec()
            window.close()
            del window
            gc.collect()
            return result
    database_path = Path(__file__).resolve().parents[1] / "mini_crm.db"
    initialize_database(database_path)
    saved = load_connection(database_path)
    try:
        config = load_config(saved) if saved else load_config()
    except ConfigurationError:
        config = AppConfig(mode=RunMode.MOCK, provider="mock")
    provider = (
        GmailProvider(
            config.google_credentials_file,
            keyring_service=config.google_keyring_service,
            keyring_user=config.google_keyring_user,
            expected_mailbox=config.test_mailbox,
            allowed_senders=config.gmail_allowed_senders,
            max_messages=config.gmail_max_messages,
            newer_than_days=config.gmail_newer_than_days,
            write_enabled=config.gmail_write_enabled,
        )
        if config.provider == "gmail"
        else MockEmailProvider()
    )
    window = MainWindow(database_path, email_provider=provider, config=config)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
