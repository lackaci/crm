"""Application entry point for the Mini CRM."""

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMainWindow

from app.db.database import initialize_database
from app.ui.customers import CustomersWidget


def main() -> int:
    """Open the Mini CRM customer window."""
    application = QApplication(sys.argv)
    window = QMainWindow()
    window.setWindowTitle("Mini CRM")
    window.resize(800, 500)
    database_path = Path(__file__).resolve().parents[1] / "mini_crm.db"
    initialize_database(database_path)
    window.setCentralWidget(CustomersWidget(database_path))
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
