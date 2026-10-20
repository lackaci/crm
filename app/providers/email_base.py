"""Interface for email providers."""

from abc import ABC, abstractmethod
from typing import Any


class EmailProvider(ABC):
    """Common interface for mock and real email services."""

    @abstractmethod
    def fetch_new_emails(self) -> list[dict[str, Any]]:
        """Return newly available email records."""

    @abstractmethod
    def send_email(self, to: str, subject: str, body: str) -> None:
        """Send an email through the configured provider."""
