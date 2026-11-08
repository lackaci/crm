"""Gmail connection settings with a separate offline configuration preview."""

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.config import ALLOWED_PROVIDERS, AppConfig, ConfigurationError, RunMode, load_config
from .privacy_preview import PrivacyPreviewWidget


class SettingsWidget(QWidget):
    """Display and edit the selected mode and provider safely."""

    def __init__(self, config: AppConfig | None = None, parent=None, database_path=None, demo_mode=False) -> None:
        super().__init__(parent)
        self.config = config or load_config()

        self.mode_combo = QComboBox()
        self.mode_combo.setObjectName("modeCombo")
        self.mode_combo.addItems([mode.value for mode in RunMode])
        self.mode_combo.setCurrentText(self.config.mode.value)

        self.provider_combo = QComboBox()
        self.provider_combo.setObjectName("providerCombo")
        self.provider_combo.addItems(sorted(ALLOWED_PROVIDERS))
        self.provider_combo.setCurrentText(self.config.provider)

        self.save_button = QPushButton("Check these settings")
        self.save_button.clicked.connect(self.apply_settings)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("settingsStatus")

        form = QFormLayout()
        form.addRow("Mode", self.mode_combo)
        form.addRow("Provider", self.provider_combo)
        content = QWidget()
        layout = QVBoxLayout(content)
        self.content_layout = layout
        self.gmail_widget = None
        if database_path is not None:
            from .gmail_connection import GmailConnectionWidget
            self.gmail_widget = GmailConnectionWidget(database_path, self.config, demo_mode=demo_mode)
            layout.addWidget(self.gmail_widget)
        preview = QWidget()
        preview_layout = QVBoxLayout(preview)
        preview_layout.addWidget(QLabel(
            "This tool only checks configuration choices. Use Gmail connection "
            "above to connect an account and apply its delivery settings."
        ))
        preview_layout.addLayout(form)
        preview_layout.addWidget(self.save_button)
        preview_layout.addWidget(self.status_label)
        preview_toggle = QToolButton()
        preview_toggle.setText("Advanced configuration preview")
        preview_toggle.setCheckable(True)
        preview_toggle.toggled.connect(preview.setVisible)
        layout.addWidget(preview_toggle)
        layout.addWidget(preview)
        preview.hide()
        self.privacy_preview_widget = PrivacyPreviewWidget()
        self.privacy_toggle = QToolButton()
        self.privacy_toggle.setText("Privacy preview (advanced)")
        self.privacy_toggle.setCheckable(True)
        self.privacy_toggle.toggled.connect(self.privacy_preview_widget.setVisible)
        layout.addWidget(self.privacy_toggle)
        layout.addWidget(self.privacy_preview_widget)
        self.privacy_preview_widget.hide()
        layout.addStretch()
        scroll = QScrollArea()
        self.scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self._show_ready()

    def _show_ready(self) -> None:
        self.status_label.setText(
            f"Selected: {self.config.mode.value} / {self.config.provider}. "
            "No API calls were made by this settings check. Sending is disabled in this preview."
        )

    def apply_settings(self) -> None:
        """Apply the visible choices without exposing or requesting secrets."""
        mode = RunMode(self.mode_combo.currentText())
        provider = self.provider_combo.currentText()
        if mode is RunMode.MOCK:
            self.config = AppConfig(mode=mode, provider=provider)
            self._show_ready()
            return
        try:
            # Validation reads environment variable names only; it does not
            # print values or initiate a provider call.
            self.config = load_config({
                "MINI_CRM_MODE": mode.value,
                "MINI_CRM_PROVIDER": provider,
            })
        except ConfigurationError:
            self.status_label.setText(
                "The selected non-Mock mode needs its required settings in "
                "the environment. No setting was applied."
            )
            return
        self._show_ready()
