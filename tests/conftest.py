"""Safety defaults shared by the test suite.

Tests must not inherit a developer's .env, database, storage directory, or
network access.  Individual tests may still pass explicit configuration when
they are testing validation of that configuration.
"""

import socket
import sys
import gc

import pytest


@pytest.fixture(autouse=True)
def isolate_mock_mode(monkeypatch, tmp_path):
    """Make every test an offline Mock-mode test with disposable paths."""
    monkeypatch.setenv("MINI_CRM_MODE", "Mock")
    monkeypatch.setenv("MINI_CRM_PROVIDER", "mock")
    monkeypatch.setenv("MINI_CRM_DATABASE_PATH", str(tmp_path / "test.sqlite"))
    monkeypatch.setenv("MINI_CRM_STORAGE_ROOT", str(tmp_path / "customer_files"))

    def blocked_connect(*args, **kwargs):
        raise AssertionError("Tests must not open a real network socket")

    monkeypatch.setattr(socket.socket, "connect", blocked_connect)


def pytest_sessionfinish(session, exitstatus):
    """Destroy native GUI objects while Qt and Python are both still alive.

    Closing a widget only hides it by default. Retained test widgets and popup
    frames otherwise survive until interpreter teardown, where Windows Qt can
    abort after pytest has already reported success. Do not override pytest's
    exit status or force a process exit: perform the missing Qt cleanup.
    """
    if "PySide6.QtWidgets" not in sys.modules:
        return
    from PySide6.QtCore import QCoreApplication, QEvent, QThread
    from PySide6.QtWidgets import QApplication
    application = QApplication.instance()
    if application is None:
        return
    windows = application.topLevelWidgets()
    for window in windows:
        for worker in window.findChildren(QThread):
            if worker.isRunning() and not worker.wait(5000):
                raise RuntimeError("A desktop test left a worker running; it cannot be safely destroyed.")
    for window in windows:
        window.close()
        window.deleteLater()
    application.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    del windows
    gc.collect()
