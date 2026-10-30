"""The main application window and its top-level screens."""

from pathlib import Path

from PySide6.QtWidgets import QMainWindow, QTabWidget

from app.config import AppConfig, load_config
from app.providers.email_base import EmailProvider
from app.providers.llm_base import LLMProvider
from .approval import ApprovalQueueWidget
from .customers import CustomersWidget
from .settings import SettingsWidget


class MainWindow(QMainWindow):
    """Hold the existing customer/inbox screen and the approval queue."""

    def __init__(
        self,
        database_path: Path,
        email_provider: EmailProvider | None = None,
        llm_provider: LLMProvider | None = None,
        parent=None,
        storage_root: Path | None = None,
        config: AppConfig | None = None,
    ) -> None:
        super().__init__(parent)
        self.database_path = database_path
        self.customers_widget = CustomersWidget(
            database_path,
            email_provider=email_provider,
            llm_provider=llm_provider,
            storage_root=storage_root,
            gmail_allowed_senders=(
                config.gmail_allowed_senders if config is not None else ()
            ),
        )
        self.approval_widget = ApprovalQueueWidget(
            database_path, provider=email_provider, sending_enabled=False
        )
        self.approval_queue = self.approval_widget
        self.settings_widget = SettingsWidget(config or load_config())
        self.privacy_preview_widget = self.settings_widget.privacy_preview_widget

        # Tabs are a simple beginner-friendly navigation sidebar alternative.
        self.tabs = QTabWidget()
        self.tabs.addTab(self.customers_widget, "Customers and Inbox")
        self.tabs.addTab(self.approval_widget, "Approval Queue")
        self.tabs.addTab(self.settings_widget, "Settings")
        self.tabs.currentChanged.connect(self._refresh_approval_queue)
        self.setCentralWidget(self.tabs)
        self.setWindowTitle("Mini CRM")
        self.resize(1000, 760)

    def _refresh_approval_queue(self, index: int) -> None:
        if index == 1:
            self.approval_widget.refresh()
