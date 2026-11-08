"""The owner-facing FAQ editor.

Qt widgets are kept deliberately thin: persistence and reply rules live in
``app.db`` and ``app.reply``. The preview follows the selected AI provider.
"""

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QLineEdit,
    QToolButton,
)

from app.db.faqs import (
    FAQEntry,
    add_faq,
    approve_faq,
    deactivate_faq,
    list_faqs,
    update_faq,
)
from app.providers.llm_fake import FakeLLMProvider
from app.reply.drafter import draft_reply
from app.reply.guardrails import check_escalation, is_meaningful_message
from app.reply.matcher import configured_threshold, find_best_faq
from app.reply.pipeline import prepare_reply
from .background import BackgroundTask


class FAQDialog(QDialog):
    """Small Add/Edit form.  The dialog does not know anything about SQL."""

    def __init__(self, parent=None, faq: FAQEntry | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit saved answer" if faq else "Add saved answer")
        self.question_input = QLineEdit()
        self.answer_input = QTextEdit()
        self.answer_input.setMinimumHeight(120)
        if faq:
            self.question_input.setText(faq.question)
            self.answer_input.setPlainText(faq.answer)

        form = QFormLayout(self)
        form.addRow("Question", self.question_input)
        form.addRow("Reply text", self.answer_input)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> tuple[str, str]:
        return self.question_input.text(), self.answer_input.toPlainText()


class FAQView(QWidget):
    """Browse and manage FAQs, including a non-persistent reply test."""

    headers = ("Question", "Status", "Last approved")

    def __init__(self, database_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.database_path = database_path
        self._faqs: list[FAQEntry] = []
        self._selected_id: int | None = None
        self.llm_provider = FakeLLMProvider()
        self.test_task = None

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(self.headers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setMinimumHeight(170)
        self.table.verticalHeader().hide()
        self.table.itemSelectionChanged.connect(self._selection_changed)

        self.add_button = QPushButton("Add answer")
        self.edit_button = QPushButton("Edit answer")
        self.approve_button = QPushButton("Approve answer")
        self.deactivate_button = QPushButton("Deactivate answer")
        self.add_button.clicked.connect(self.add)
        self.edit_button.clicked.connect(self.edit)
        self.approve_button.clicked.connect(self.approve)
        self.deactivate_button.clicked.connect(self.deactivate)
        actions = QHBoxLayout()
        for button in (self.add_button, self.edit_button,
                       self.approve_button, self.deactivate_button):
            actions.addWidget(button)
        actions.addStretch()

        self.sample_email_input = QTextEdit()
        self.sample_email_input.setPlaceholderText("Paste a sample customer email here.")
        self.sample_email_input.setMinimumHeight(80)
        self.test_button = QPushButton("Try this message")
        self.test_button.clicked.connect(self.test_sample)
        self.decision_label = QLabel("Decision: —")
        self.matched_label = QLabel("Matched FAQ: —")
        self.score_label = QLabel("Score: —")
        self.draft_label = QLabel("Draft text: —")
        self.draft_label.setWordWrap(True)
        test_form = QFormLayout()
        test_form.addRow("Sample customer email", self.sample_email_input)
        test_form.addRow(self.test_button)
        test_form.addRow(self.decision_label)
        test_form.addRow(self.matched_label)
        test_form.addRow(self.score_label)
        test_form.addRow(self.draft_label)
        test_box = QGroupBox("Try a message (nothing is saved)")
        test_box.setLayout(test_form)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Only approved answers can be used in replies."))
        layout.addWidget(self.table)
        layout.addLayout(actions)
        self.test_toggle = QToolButton()
        self.test_toggle.setText("Try a sample message")
        self.test_toggle.setCheckable(True)
        self.test_toggle.toggled.connect(test_box.setVisible)
        layout.addWidget(self.test_toggle)
        layout.addWidget(test_box)
        test_box.hide()
        self._selection_changed()
        self.refresh()

    def refresh(self) -> None:
        self._faqs = list_faqs(self.database_path)
        self.table.setRowCount(len(self._faqs))
        for row, faq in enumerate(self._faqs):
            self.table.setItem(row, 0, QTableWidgetItem(faq.question))
            self.table.setItem(row, 1, QTableWidgetItem(faq.status))
            self.table.setItem(row, 2, QTableWidgetItem(faq.approved_at or "—"))
            self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, faq.id)
        self.table.resizeColumnsToContents()
        self._selection_changed()

    def _selection_changed(self) -> None:
        row = self.table.currentRow()
        self._selected_id = (
            int(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole))
            if row >= 0 and self.table.item(row, 0) else None
        )
        selected = self._selected()
        enabled = selected is not None
        self.edit_button.setEnabled(enabled)
        self.approve_button.setEnabled(enabled and selected.status != "approved")
        self.deactivate_button.setEnabled(enabled and selected.status != "inactive")

    def _selected(self) -> FAQEntry | None:
        return next((faq for faq in self._faqs if faq.id == self._selected_id), None)

    def _error(self, title: str, error: Exception) -> None:
        QMessageBox.warning(self, title, str(error))

    def add(self) -> None:
        dialog = FAQDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            add_faq(self.database_path, *dialog.values())
        except (ValueError, sqlite3.Error) as error:
            self._error("Could not add FAQ", error)
            return
        self.refresh()

    def edit(self) -> None:
        faq = self._selected()
        if faq is None:
            return
        dialog = FAQDialog(self, faq)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            update_faq(self.database_path, faq.id, *dialog.values())
        except (ValueError, sqlite3.Error) as error:
            self._error("Could not save FAQ", error)
            return
        self.refresh()

    def approve(self) -> None:
        faq = self._selected()
        if faq is None:
            return
        if QMessageBox.question(
            self, "Approve answer", "Approve this answer for use in draft replies?"
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            approve_faq(self.database_path, faq.id)
        except (ValueError, sqlite3.Error) as error:
            self._error("Could not approve FAQ", error)
            return
        self.refresh()

    def deactivate(self) -> None:
        faq = self._selected()
        if faq is None:
            return
        if QMessageBox.question(
            self, "Deactivate answer",
            "Stop using this saved answer in new replies?"
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            deactivate_faq(self.database_path, faq.id)
        except (ValueError, sqlite3.Error) as error:
            self._error("Could not deactivate FAQ", error)
            return
        self.refresh()

    def test_sample(self) -> None:
        """Run matching and drafting without calling process_email (which saves)."""
        text = self.sample_email_input.toPlainText().strip()
        # A neutral name makes FakeLLM's deterministic greeting valid without
        # inventing any customer identity or contacting a provider.
        email = SimpleNamespace(subject="", body=text, from_name="Customer")
        self.matched_label.setText("Matched FAQ: —")
        self.score_label.setText("Score: —")
        self.draft_label.setText("Draft text: —")
        if getattr(self.llm_provider, "uses_network", False):
            if self.test_task is not None:
                return
            # Virtual ID: prepare_reply is pure and this preview never saves.
            email.id = 0
            provider = self.llm_provider
            self.test_task = BackgroundTask(lambda: prepare_reply(email, self.database_path, provider), self)
            self.test_task.succeeded.connect(self._online_preview_complete)
            self.test_task.failed.connect(self._show_escalation)
            self.test_task.finished.connect(self._online_preview_finished)
            self.test_button.setEnabled(False)
            self.decision_label.setText("Decision: checking with the selected online model…")
            self.test_task.start()
            return

        # Matching is safe and read-only in this box.  Do it before the
        # guardrail decision so an escalated sample still explains which FAQ
        # would have been the closest match and how strong that match was.
        match = find_best_faq(
            email, list_faqs(self.database_path, "approved"), FakeLLMProvider()
        ) if is_meaningful_message(email) else None
        if match is not None:
            self.matched_label.setText(
                f"Matched FAQ: {match.faq.question if match.faq else 'None'}"
            )
            self.score_label.setText(f"Score: {match.score:.2f}")

        escalation = check_escalation(email)
        if escalation:
            self._show_escalation(escalation.reason)
            return
        if not is_meaningful_message(email):
            self._show_escalation("email is empty or unclear")
            return
        assert match is not None
        if match.faq is None or match.score < configured_threshold():
            self._show_escalation("no sufficiently confident approved FAQ match")
            return
        try:
            result = draft_reply(email, match.faq, FakeLLMProvider())
        except (ValueError, RuntimeError) as error:
            self._show_escalation(str(error))
            return
        self.decision_label.setText("Decision: Draft")
        self.draft_label.setText(f"Draft text: {result.reply}")

    def _show_escalation(self, reason: str) -> None:
        self.decision_label.setText(f"Decision: Escalate — {reason}")

    def _online_preview_complete(self, result):
        if result.match:
            self.matched_label.setText("Matched FAQ: " + (result.faq.question if result.faq else "None"))
            self.score_label.setText(f"Score: {result.match.score:.2f}")
        if result.status == "pending":
            self.decision_label.setText("Decision: Draft")
            self.draft_label.setText("Draft text: " + result.body)
        else:
            self._show_escalation(result.reason or "Please review personally.")

    def _online_preview_finished(self):
        old, self.test_task = self.test_task, None
        if old:
            old.deleteLater()
        self.test_button.setEnabled(True)


# Friendly aliases make the view easy to discover for callers and tests.
FAQsWidget = FAQView
FAQWidget = FAQView
