"""Offline email provider backed by the sample JSON file."""

import json
from pathlib import Path
from typing import Any

from .email_base import EmailDeliveryError, EmailProvider


class MockEmailProvider(EmailProvider):
    """Reads sample emails and emits no customer message contents."""

    provider_name = "mock"

    def __init__(self, data_path: Path | None = None) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.data_path = data_path or project_root / "sample_data" / "emails.json"

    def fetch_new_emails(self) -> list[dict[str, Any]]:
        """Load the sample email records from disk."""
        with self.data_path.open(encoding="utf-8") as email_file:
            records = json.load(email_file)
        if not isinstance(records, list):
            raise ValueError("Sample email data must be a JSON list.")
        return records

    def send_email(self, to: str, subject: str, body: str) -> None:
        """Sending is unavailable at the provider boundary during M11."""
        raise EmailDeliveryError("Sending email is disabled in this milestone.")
