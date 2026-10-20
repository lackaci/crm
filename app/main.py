"""Application entry point for the first milestone."""

import sys

from PySide6.QtWidgets import QApplication, QMainWindow


def main() -> int:
    """Open the empty Mini CRM window."""
    application = QApplication(sys.argv)
    window = QMainWindow()
    window.setWindowTitle("Mini CRM")
    window.resize(800, 500)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
