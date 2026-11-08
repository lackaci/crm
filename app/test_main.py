"""Launch the persistent, isolated test version of Mini CRM."""
import argparse
import gc
from pathlib import Path
import sys

from PySide6.QtWidgets import QApplication
from app.config import AppConfig, ConfigurationError, RunMode, load_config
from app.db.connections import load_connection
from app.providers.factory import email_provider
from app.test_workflow import create_test_workspace
from app.ui.test_workspace import TestWindow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[1] / "test_workspace")
    args = parser.parse_args()
    application = QApplication.instance() or QApplication(sys.argv[:1])
    database, storage = create_test_workspace(args.workspace)
    saved = load_connection(database)
    default = AppConfig(RunMode.MOCK, "mock", google_keyring_service="mini-crm-test")
    try:
        config = load_config(saved) if saved else default
    except ConfigurationError:
        config = default
    window = TestWindow(database, storage_root=storage, config=config, email_provider=email_provider(config))
    window.show()
    result = application.exec()
    window.close()
    del window
    gc.collect()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
