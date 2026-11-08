"""Render only an isolated, fictional test setup; no Gmail or online AI calls."""
from pathlib import Path
from tempfile import TemporaryDirectory
import gc

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase
from app.config import AppConfig, RunMode
from app.test_workflow import create_test_workspace
from app.ui.test_workspace import TestWindow


def main():
    app = QApplication.instance() or QApplication([])
    font = Path("C:/Windows/Fonts/segoeui.ttf")
    if font.is_file():
        QFontDatabase.addApplicationFont(str(font))
    output = Path(__file__).resolve().parents[1] / "docs" / "previews"
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="mini-crm-test-preview-") as folder:
        database, storage = create_test_workspace(Path(folder))
        window = TestWindow(database, storage_root=storage, config=AppConfig(RunMode.MOCK, "mock"))
        window.resize(1100, 800)
        window.show()
        app.processEvents()
        assert window.grab().save(str(output / "test-setup.png"))
        window.resize(900, 620)
        app.processEvents()
        assert window.grab().save(str(output / "test-setup-small.png"))
        window.close()
        del window
        gc.collect()


if __name__ == "__main__":
    main()
