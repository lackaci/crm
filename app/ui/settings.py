"""Offline settings panel for M08.

This panel edits configuration choices only.  It never creates a provider or
performs an external call.
"""

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.config import ALLOWED_PROVIDERS, AppConfig, ConfigurationError, RunMode, load_config
from .privacy_preview import PrivacyPreviewWidget


class SettingsWidget(QWidget):
    """Display and edit the selected mode and provider safely."""

    def __init__(self, config: AppConfig | None = None, parent=None) -> None:
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

        self.save_button = QPushButton("Apply settings")
        self.save_button.clicked.connect(self.apply_settings)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("settingsStatus")

        form = QFormLayout()
        form.addRow("Mode", self.mode_combo)
        form.addRow("Provider", self.provider_combo)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Integration settings"))
        layout.addWidget(QLabel(
            "M08 is offline-only. Sending is disabled in every mode; "
            "Gmail and cloud providers are names for future milestones."
        ))
        layout.addLayout(form)
        layout.addWidget(self.save_button)
        layout.addWidget(self.status_label)
        self.privacy_preview_widget = PrivacyPreviewWidget()
        layout.addWidget(self.privacy_preview_widget)
        layout.addStretch()
        self._show_ready()

    def _show_ready(self) -> None:
        self.status_label.setText(
            f"Selected: {self.config.mode.value} / {self.config.provider}. "
            "No API calls are made. Sending is disabled."
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
