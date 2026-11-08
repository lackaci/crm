"""Desktop connection metadata only; OAuth tokens belong in OS keyring."""
import json
from pathlib import Path
from .database import open_database

ALLOWED_KEYS = frozenset({"MINI_CRM_MODE", "MINI_CRM_PROVIDER", "MINI_CRM_TEST_MAILBOX",
    "MINI_CRM_GOOGLE_CREDENTIALS_FILE", "MINI_CRM_GOOGLE_KEYRING_SERVICE", "MINI_CRM_GOOGLE_KEYRING_USER",
    "MINI_CRM_GMAIL_ALLOWED_SENDERS", "MINI_CRM_GMAIL_MAX_MESSAGES", "MINI_CRM_GMAIL_NEWER_THAN_DAYS",
    "MINI_CRM_GMAIL_WRITE_ENABLED", "MINI_CRM_ENABLE_SENDING", "MINI_CRM_SEND_ALLOWLIST"})


def load_connection(database_path: Path):
    with open_database(database_path) as connection:
        row = connection.execute("SELECT configuration FROM desktop_mail_settings WHERE id=1").fetchone()
    return (json.loads(row["configuration"]) or None) if row else None


def save_connection(database_path: Path, values):
    if set(values) - ALLOWED_KEYS or not all(isinstance(value, str) for value in values.values()):
        raise ValueError("Only non-secret Gmail connection settings may be stored")
    with open_database(database_path) as connection:
        connection.execute("INSERT INTO desktop_mail_settings(id,configuration) VALUES(1,?) "
            "ON CONFLICT(id) DO UPDATE SET configuration=excluded.configuration", (json.dumps(values),))
        connection.execute("INSERT INTO audit_log(actor,action,details) VALUES('owner','gmail_settings_saved','connection metadata updated')")


def mail_paused(database_path: Path):
    with open_database(database_path) as connection:
        row = connection.execute("SELECT paused FROM desktop_mail_settings WHERE id=1").fetchone()
    return bool(row and row["paused"])


def set_mail_paused(database_path: Path, paused: bool):
    with open_database(database_path) as connection:
        connection.execute("INSERT INTO desktop_mail_settings(id,configuration,paused) VALUES(1,'{}',?) "
            "ON CONFLICT(id) DO UPDATE SET paused=excluded.paused", (int(paused),))
        connection.execute("INSERT INTO audit_log(actor,action,details) VALUES('owner',?,?)",
            ("mail_paused" if paused else "mail_resumed", "desktop mail activity"))
