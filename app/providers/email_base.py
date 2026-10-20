"""Interface for email providers."""

from abc import ABC, abstractmethod
from typing import Any


class EmailDeliveryError(Exception):
    """Expected failure while delivering an email through a provider."""


class EmailProvider(ABC):
    """Common interface for mock and real email services."""

    @abstractmethod
    def fetch_new_emails(self) -> list[dict[str, Any]]:
        """Return newly available email records."""

    @abstractmethod
    def send_email(self, to: str, subject: str, body: str) -> None:
        """Send an email, raising :class:`EmailDeliveryError` on delivery failure."""
