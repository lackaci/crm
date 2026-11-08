"""Focused tests for the offline M08 settings screen."""

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from app.config import AppConfig, RunMode
from app.db.database import initialize_database
from app.ui.main_window import MainWindow
from app.ui.settings import SettingsWidget


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def test_settings_displays_and_changes_mock_choices(qapp):
    widget = SettingsWidget()
    assert widget.mode_combo.currentText() == "Mock"
    assert "Sending is disabled" in widget.status_label.text()

    widget.provider_combo.setCurrentText("fake")
    widget.apply_settings()

    assert widget.config.provider == "fake"
    assert widget.config.mode is RunMode.MOCK
    assert "No API calls" in widget.status_label.text()


def test_main_window_disables_send_in_m08(qapp, tmp_path: Path):
    database_path = tmp_path / "settings-ui.db"
    initialize_database(database_path)
    window = MainWindow(
        database_path,
        config=AppConfig(mode=RunMode.MOCK, provider="mock"),
    )

    assert window.pages.count() == 8
    assert window.pages.widget(7) is window.replied_widget
    assert window.navigation.item(7).text() == 'Replied'
    assert window.navigation.count() == 8
    assert not window.approval_widget.approve_button.isEnabled()
    assert "Sending is disabled" in window.approval_widget.approve_button.toolTip()
