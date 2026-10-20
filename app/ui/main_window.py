"""The main application window and its top-level screens."""

from pathlib import Path

from PySide6.QtWidgets import QMainWindow, QTabWidget

from app.providers.email_base import EmailProvider
from app.providers.llm_base import LLMProvider
from .approval import ApprovalQueueWidget
from .customers import CustomersWidget


class MainWindow(QMainWindow):
    """Hold the existing customer/inbox screen and the approval queue."""

    def __init__(
        self,
        database_path: Path,
        email_provider: EmailProvider | None = None,
        llm_provider: LLMProvider | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.database_path = database_path
        self.customers_widget = CustomersWidget(
            database_path,
            email_provider=email_provider,
            llm_provider=llm_provider,
        )
        self.approval_widget = ApprovalQueueWidget(
            database_path, provider=email_provider
        )
        self.approval_queue = self.approval_widget

        # Tabs are a simple beginner-friendly navigation sidebar alternative.
        self.tabs = QTabWidget()
        self.tabs.addTab(self.customers_widget, "Customers and Inbox")
        self.tabs.addTab(self.approval_widget, "Approval Queue")
        self.tabs.currentChanged.connect(self._refresh_approval_queue)
        self.setCentralWidget(self.tabs)
        self.setWindowTitle("Mini CRM")
        self.resize(900, 700)

    def _refresh_approval_queue(self, index: int) -> None:
        if index == 1:
            self.approval_widget.refresh()
