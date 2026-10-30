"""Debug-only, non-persistent preview of text sent to an LLM."""

from PySide6.QtWidgets import QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget

from app.privacy import masked_preview


class PrivacyPreviewWidget(QWidget):
    """Show masked text only; this widget never writes to the database."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.system_input = QTextEdit()
        self.prompt_input = QTextEdit()
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        button = QPushButton("Preview what we send (masked)")
        button.clicked.connect(self.preview)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("System text (debug only)"))
        layout.addWidget(self.system_input)
        layout.addWidget(QLabel("Prompt text (debug only)"))
        layout.addWidget(self.prompt_input)
        layout.addWidget(button)
        layout.addWidget(QLabel("Masked preview"))
        layout.addWidget(self.output)

    def preview(self):
        system, prompt = masked_preview(
            self.system_input.toPlainText(), self.prompt_input.toPlainText()
        )
        self.output.setPlainText(f"SYSTEM:\n{system}\n\nPROMPT:\n{prompt}")
