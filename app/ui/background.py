"""Small Qt worker for explicitly requested account/import/delivery operations."""
from PySide6.QtCore import QThread, Signal
from app.config import ConfigurationError
from app.providers.email_base import EmailDeliveryError


class BackgroundTask(QThread):
    progress = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            result = self.operation()
        except (ValueError, ConfigurationError, EmailDeliveryError) as error:
            self.failed.emit(str(error))
        except Exception:
            self.failed.emit("The operation could not be completed. Check the connection and delivery status.")
        else:
            self.succeeded.emit(result)
