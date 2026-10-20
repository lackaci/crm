"""Application entry point for the Mini CRM."""

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.db.database import initialize_database
from app.ui.main_window import MainWindow


def main() -> int:
    """Open the Mini CRM customer window."""
    application = QApplication(sys.argv)
    database_path = Path(__file__).resolve().parents[1] / "mini_crm.db"
    initialize_database(database_path)
    window = MainWindow(database_path)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
