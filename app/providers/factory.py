from app.config import AppConfig
from .gmail import GmailProvider
from .email_mock import MockEmailProvider
from .email_base import EmailProvider


def email_provider(config: AppConfig):
    if config.provider != "gmail":
        return MockEmailProvider()
    return GmailProvider(config.google_credentials_file, keyring_service=config.google_keyring_service,
        keyring_user=config.google_keyring_user, expected_mailbox=config.test_mailbox,
        allowed_senders=config.gmail_allowed_senders, max_messages=config.gmail_max_messages,
        newer_than_days=config.gmail_newer_than_days, write_enabled=config.gmail_write_enabled)


class BackgroundGmailImport(EmailProvider):
    """Scheduled imports may refresh a token but never open Google sign-in."""
    provider_name = "gmail"

    def __init__(self, provider):
        self.provider = provider
        self.expected_mailbox = provider.expected_mailbox

    def fetch_new_emails(self):
        return self.provider.fetch_new_emails(allow_login=False)

    def import_incrementally(self, database_path, *, storage_root=None, progress=None):
        return self.provider.import_incrementally(database_path, storage_root=storage_root, progress=progress)
