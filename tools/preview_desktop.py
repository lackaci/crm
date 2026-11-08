"""Render the fictional desktop demo for layout review, never the real workspace."""
import gc
from pathlib import Path
from tempfile import TemporaryDirectory
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase
from app.config import AppConfig, RunMode
from app.db.database import open_database
from app.db import automation as automation_store
from app.automation import run_automation
from app.documents import process_documents
from app.providers.document_model import DemoDocumentExtractor
from app.demo import create_demo_workspace
from app.providers.gmail import GmailProvider
from app.providers.email_mock import MockEmailProvider
from app.providers.llm_fake import FakeLLMProvider
from app.ui.main_window import MainWindow


def main():
    application = QApplication.instance() or QApplication([])
    # Windows' offscreen Qt plugin may not enumerate system fonts. Register
    # the existing system font for rendering; no font file is copied/bundled.
    system_font = Path("C:/Windows/Fonts/segoeui.ttf")
    if system_font.is_file():
        QFontDatabase.addApplicationFont(str(system_font))
    output = Path(__file__).resolve().parents[1] / "docs" / "previews"
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="mini-crm-preview-") as folder:
        database, storage = create_demo_workspace(Path(folder))
        window = MainWindow(database, email_provider=MockEmailProvider(), llm_provider=FakeLLMProvider(),
            storage_root=storage, config=AppConfig(RunMode.MOCK, "mock"), demo_mode=True)
        window.resize(1100, 760)
        window.show()
        application.processEvents()
        assert window.grab().save(str(output / "inbox.png"))
        window.inbox_widget.create_draft_button.click()
        application.processEvents()
        assert window.pages.currentWidget() is window.approval_widget
        assert window.grab().save(str(output / "review.png"))
        window.navigate(1)
        application.processEvents()
        assert window.grab().save(str(output / "customers.png"))
        window.navigate(0)
        window.resize(900, 620)
        application.processEvents()
        assert window.grab().save(str(output / "inbox-small.png"))
        member = automation_store.add_staff(database, "Sam (demo)")
        rules = automation_store.list_rules(database)
        rules["question"]["staff_id"] = member
        automation_store.save_rules(database, rules)
        automation_store.save_settings(database, enabled=True)
        run_automation(database)
        window._automation_complete()
        window.automation_widget.load_settings()
        window.navigate(6)
        window.resize(1100, 760)
        application.processEvents()
        if window.automation_widget.history_table.rowCount():
            window.automation_widget.history_table.selectRow(0)
        assert window.grab().save(str(output / "automation.png"))
        window.resize(900, 620)
        application.processEvents()
        assert window.grab().save(str(output / "automation-small.png"))
        window.automation_widget.rules_toggle.setChecked(True)
        application.processEvents()
        assert window.grab().save(str(output / "automation-rules.png"))
        process_documents(database, storage, DemoDocumentExtractor(), workbook_path=Path(folder) / "exports" / "crm_documents.xlsx")
        window.navigate(4)
        window.files_widget.file_tabs.setCurrentIndex(1)
        window.files_widget.documents_widget.refresh()
        window.resize(1100, 760)
        application.processEvents()
        assert window.grab().save(str(output / "documents.png"))
        window.resize(900, 620)
        application.processEvents()
        assert window.grab().save(str(output / "documents-small.png"))
        print("Demo previews saved; small-window size:", window.width(), window.height())
        window.close()
        del window
        gc.collect()

        # Render Gmail controls with fictional data and an inert injected
        # service. No login, import, draft creation or send is performed.
        with open_database(database) as connection:
            connection.execute("UPDATE emails SET provider='gmail',source_mailbox='demo@example.invalid'")
        connection.close()
        automation_store.save_settings(database, enabled=False)
        window = MainWindow(database, email_provider=GmailProvider(service=object(),
            expected_mailbox="demo@example.invalid", write_enabled=True),
            llm_provider=FakeLLMProvider(), storage_root=storage,
            config=AppConfig(RunMode.DRY_RUN, "gmail", test_mailbox="demo@example.invalid", gmail_write_enabled=True))
        window.resize(1100, 760)
        window.show()
        window.navigate(5)
        application.processEvents()
        assert window.grab().save(str(output / "gmail-settings.png"))
        window.navigate(2)
        application.processEvents()
        assert window.grab().save(str(output / "gmail-review.png"))
        window.resize(900, 620)
        application.processEvents()
        assert window.grab().save(str(output / "gmail-review-small.png"))
        window.navigate(4)
        window.files_widget.file_tabs.setCurrentIndex(1)
        window.resize(1100, 760)
        application.processEvents()
        assert window.grab().save(str(output / "online-ai.png"))
        window.resize(900, 620)
        application.processEvents()
        assert window.grab().save(str(output / "online-ai-small.png"))
        control = window.files_widget.documents_widget
        control.provider_combo.setCurrentIndex(control.provider_combo.findData("vercel"))
        control.replies_checkbox.setChecked(True)
        application.processEvents()
        assert window.grab().save(str(output / "vercel-ai-small.png"))
        window.close()
        del window
        gc.collect()


if __name__ == "__main__":
    main()
