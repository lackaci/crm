"""Interface for email providers."""

from abc import ABC, abstractmethod
from typing import Any
from dataclasses import dataclass


class EmailDeliveryError(Exception):
    """Expected failure while delivering an email through a provider."""


class DeliveryRejected(EmailDeliveryError):
    """The operation is known not to have been accepted."""


class DeliveryUncertain(EmailDeliveryError):
    """An external mutation may have succeeded; never retry blindly."""


@dataclass(frozen=True)
class ReviewedReply:
    sender: str
    recipient: str
    subject: str
    body: str
    rfc_message_id: str
    thread_id: str | None = None
    original_message_id: str | None = None


class EmailProvider(ABC):
    """Common interface for mock and real email services."""

    @abstractmethod
    def fetch_new_emails(self) -> list[dict[str, Any]]:
        """Return newly available email records."""

    def send_email(self, to: str, subject: str, body: str) -> None:
        """Sending is deliberately unavailable until the later sending milestone."""
        raise EmailDeliveryError("Sending email is disabled in this milestone.")
