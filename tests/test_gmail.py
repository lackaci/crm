"""Offline M11 Gmail and import tests."""

from pathlib import Path

from app.db.database import initialize_database, open_database
from app.db.emails import import_emails
from app.providers.gmail import GMAIL_READONLY_SCOPE, GmailProvider


class FakeRequest:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class FakeAttachments:
    def get(self, **kwargs):
        return FakeRequest({"data": "YXR0YWNobWVudA=="})


class FakeMessages:
    def __init__(self, message):
        self.message = message

    def list(self, **kwargs):
        return FakeRequest({"messages": [{"id": self.message["id"]}]})

    def get(self, **kwargs):
        return FakeRequest(self.message)

    def attachments(self):
        return FakeAttachments()


class FakeUsers:
    def __init__(self, message):
        self._messages = FakeMessages(message)

    def messages(self):
        return self._messages


class ProfileFakeUsers(FakeUsers):
    def __init__(self, message, email_address):
        super().__init__(message)
        self.email_address = email_address

    def getProfile(self, **kwargs):
        return FakeRequest({"emailAddress": self.email_address})


class FakeService:
    def __init__(self, message):
        self._users = FakeUsers(message)

    def users(self):
        return self._users


class ProfileFakeService(FakeService):
    def __init__(self, message, email_address):
        self._users = ProfileFakeUsers(message, email_address)


class PaginatedMessages(FakeMessages):
    def __init__(self, message):
        super().__init__(message)
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("pageToken") == "page-2":
            return FakeRequest({"messages": [{"id": self.message["id"]}]})
        return FakeRequest(
            {"messages": [{"id": "first"}], "nextPageToken": "page-2"}
        )

    def get(self, **kwargs):
        if kwargs["id"] == "first":
            first = dict(self.message)
            first["id"] = "first"
            return FakeRequest(first)
        return FakeRequest(self.message)


class PaginatedService(FakeService):
    def __init__(self, message):
        self._users = FakeUsers(message)
        self._users._messages = PaginatedMessages(message)


def test_gmail_normalizes_and_imports_idempotently(tmp_path):
    database = tmp_path / "crm.db"
    initialize_database(database)
    with open_database(database) as connection:
        connection.execute(
            "INSERT INTO customers (name, email) VALUES (?, ?)",
            ("Anna", "anna@example.com"),
        )
    message = {
        "id": "message-1",
        "threadId": "thread-1",
        "labelIds": ["INBOX"],
        "payload": {
            "headers": [
                {"name": "From", "value": "Anna <anna@example.com>"},
                {"name": "Subject", "value": "Hello"},
                {"name": "Date", "value": "Tue, 27 Oct 2026 10:00:00 +0000"},
            ],
            "mimeType": "multipart/mixed",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": "SGk="}},
                {
                    "filename": "quote.txt",
                    "mimeType": "text/plain",
                    "body": {"attachmentId": "attachment-1"},
                },
            ],
        },
    }
    provider = GmailProvider(service=FakeService(message))
    records = provider.fetch_new_emails()
    assert records[0]["remote_message_id"] == "message-1"
    assert records[0]["attachments"][0]["content"] == b"attachment"
    assert GMAIL_READONLY_SCOPE == "https://www.googleapis.com/auth/gmail.readonly"
    assert import_emails(database, provider, storage_root=tmp_path / "files") == 1
    assert import_emails(database, provider, storage_root=tmp_path / "files") == 0
    with open_database(database) as connection:
        email = connection.execute(
            "SELECT customer_id, remote_thread_id FROM emails"
        ).fetchone()
        attachment = connection.execute(
            "SELECT source_email_id FROM files"
        ).fetchone()
    assert email["customer_id"] == 1
    assert email["remote_thread_id"] == "thread-1"
    assert attachment["source_email_id"] == 1


def test_gmail_paginates_and_applies_limit_and_date_query():
    message = {
        "id": "second",
        "payload": {
            "headers": [{"name": "From", "value": "SWTOR <swtor@example.com>"}],
            "mimeType": "text/plain",
            "body": {"data": "SGk="},
        },
    }
    service = PaginatedService(message)
    provider = GmailProvider(
        service=service,
        allowed_senders=("swtor@example.com",),
        max_messages=2,
        newer_than_days=7,
    )

    records = provider.fetch_new_emails()

    assert len(records) == 2
    calls = service.users().messages().calls
    assert calls[0]["q"] == "from:swtor@example.com newer_than:7d"
    assert calls[1]["pageToken"] == "page-2"


def test_gmail_never_sends():
    provider = GmailProvider(service=object())
    try:
        provider.send_email("nobody@example.com", "x", "x")
    except Exception as error:
        assert "disabled" in str(error).lower()
    else:
        raise AssertionError("Gmail sending must stay disabled")


def test_gmail_converts_html_only_body_to_readable_text():
    html = "<html><style>.hidden{display:none}</style><body><h1>Reward</h1><p>Hello <b>KOTOR</b>.</p></body></html>"
    message = {
        "id": "html-message",
        "payload": {
            "headers": [],
            "mimeType": "text/html",
            "body": {"data": "PGh0bWw+PHN0eWxlPi5oaWRkZW57ZGlzcGxheTpub25lfTwvc3R5bGU+PGJvZHk+PGgxPlJld2FyZDwvaDE+PHA+SGVsbG8gPGI+S09UT1I8L2I+LjwvcD48L2JvZHk+PC9odG1sPg=="},
        },
    }

    record = GmailProvider(service=ProfileFakeService(message, "crm-test@example.invalid")).fetch_new_emails()[0]

    assert record["body"] == "Reward\n\nHello KOTOR."


def test_gmail_rejects_a_mismatched_authenticated_mailbox():
    provider = GmailProvider(
        service=ProfileFakeService({"id": "unused"}, "other@example.invalid"),
        expected_mailbox="CRM-Test@Example.Invalid",
    )
    try:
        provider.fetch_new_emails()
    except Exception as error:
        assert "could not be verified" in str(error)
        assert "other@example.invalid" not in str(error)
        assert "CRM-Test@Example.Invalid" not in str(error)
    else:
        raise AssertionError("Gmail must reject a mismatched mailbox")


def test_gmail_accepts_authenticated_mailbox_case_insensitively():
    message = {
        "id": "profile-message",
        "payload": {
            "headers": [],
            "mimeType": "text/plain",
            "body": {"data": "SGk="},
        },
    }
    provider = GmailProvider(
        service=ProfileFakeService(message, "crm-test@example.invalid"),
        expected_mailbox="CRM-TEST@EXAMPLE.INVALID",
    )
    assert provider.fetch_new_emails()[0]["remote_message_id"] == "profile-message"


def test_oauth_uses_keyring_and_read_only_scope(tmp_path):
    credentials_file = tmp_path / "client.json"
    credentials_file.write_text("{}", encoding="utf-8")
    calls = []

    class Credentials:
        valid = False

        @classmethod
        def from_authorized_user_info(cls, info, scopes):
            calls.append(("read", info, scopes))
            return cls()

        def to_json(self):
            return '{"access_token":"redacted-for-test"}'

    class Keyring:
        def get_password(self, service, user):
            calls.append(("get", service, user))
            return None

        def set_password(self, service, user, value):
            calls.append(("set", service, user, value))

    class Flow:
        def run_local_server(self, port):
            calls.append(("login", port))
            return Credentials()

    provider = GmailProvider(
        credentials_file,
        keyring_backend=Keyring(),
        flow_factory=lambda path, scopes: (
            calls.append(("flow", path, scopes)) or Flow()
        ),
        service_builder=lambda name, version, credentials: (
            calls.append(("build", name, version, credentials)) or object()
        ),
        keyring_service="test-service",
        keyring_user="test-user",
    )

    # Avoid importing optional Google packages in this deterministic test.
    import sys
    import types
    google = types.ModuleType("google")
    oauth2 = types.ModuleType("google.oauth2")
    credentials_module = types.ModuleType("google.oauth2.credentials")
    credentials_module.Credentials = Credentials
    old_modules = {name: sys.modules.get(name) for name in (
        "google", "google.oauth2", "google.oauth2.credentials"
    )}
    sys.modules.update({
        "google": google,
        "google.oauth2": oauth2,
        "google.oauth2.credentials": credentials_module,
    })
    try:
        provider._service_or_login()
    finally:
        for name, module in old_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    assert ("flow", str(credentials_file), [GMAIL_READONLY_SCOPE]) in calls
    assert ("set", "test-service", "test-user", '{"access_token":"redacted-for-test"}') in calls
    assert not (tmp_path / "token.json").exists()
