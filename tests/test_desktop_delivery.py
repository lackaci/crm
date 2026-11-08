"""All Gmail mutations use fake services; real network is blocked by conftest."""
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from email import policy
from email.parser import BytesParser
from types import SimpleNamespace
import time

import pytest
from PySide6.QtWidgets import QApplication

from app.config import AppConfig, RunMode
from app.db.database import initialize_database, open_database
from app.db.emails import import_emails
from app.db.drafts import create_draft_reply, get_draft
from app.db.connections import load_connection, set_mail_paused
from app.providers.email_base import DeliveryRejected, DeliveryUncertain
from app.providers.gmail import GmailProvider
from app.reply import delivery
from app.reply.approval import edit_draft
from app.ui.main_window import MainWindow


class Crash(BaseException):
    pass


class FakeRequest:
    def __init__(self, operation):
        self.operation = operation
    def execute(self, **kwargs):
        assert kwargs.get("num_retries", 0) == 0
        return self.operation()


class FakeGmail:
    def __init__(self):
        self.account = "owner@example.invalid"
        self.drafts_store, self.sent_store = {}, {}
        self.create_calls, self.send_calls = 0, 0
        self.uncertain_send = False
        self.crash_create = False
        self.reject_send = False
        self.raw_sent = None
    def users(self):
        return self
    def getProfile(self, **kwargs):
        return FakeRequest(lambda: {"emailAddress": self.account})
    def messages(self):
        return FakeMessages(self)
    def drafts(self):
        return FakeDrafts(self)


class FakeMessages:
    def __init__(self, service):
        self.service = service
    def get(self, **kwargs):
        return FakeRequest(lambda: {"id": "m1", "threadId": "t1", "payload": {"headers": [
            {"name": "From", "value": "Customer <customer@example.invalid>"},
            {"name": "Message-ID", "value": "<incoming@example.invalid>"}],
            "mimeType": "text/plain", "body": {"data": "SGVsbG8="}}})
    def list(self, **kwargs):
        def run():
            if "rfc822msgid:" in kwargs.get("q", ""):
                store = self.service.sent_store if "in:sent" in kwargs["q"] else self.service.drafts_store
                return {"messages": [{"id": value["message_id"]} for value in store.values()]}
            return {"messages": [{"id": "m1"}]}
        return FakeRequest(run)


class FakeDrafts:
    def __init__(self, service):
        self.service = service
    def create(self, **kwargs):
        def run():
            self.service.create_calls += 1
            self.service.drafts_store["draft-1"] = {"message_id": "draft-message-1", "message": kwargs["body"]["message"]}
            if self.service.crash_create:
                self.service.crash_create = False
                raise Crash()
            return {"id": "draft-1"}
        return FakeRequest(run)
    def send(self, **kwargs):
        def run():
            self.service.send_calls += 1
            if self.service.reject_send:
                error = RuntimeError("RAW SECRET MUST NOT LEAK")
                error.resp = SimpleNamespace(status=403)
                raise error
            self.service.raw_sent = kwargs["body"]["message"]["raw"]
            self.service.sent_store["sent-1"] = {"message_id": "sent-1"}
            self.service.drafts_store.pop(kwargs["body"]["id"], None)
            if self.service.uncertain_send:
                raise TimeoutError("RAW SECRET MUST NOT LEAK")
            return {"id": "sent-1"}
        return FakeRequest(run)
    def list(self, **kwargs):
        return FakeRequest(lambda: {"drafts": [{"id": key, "message": {"id": value["message_id"]}} for key, value in self.service.drafts_store.items()]})
    def delete(self, **kwargs):
        return FakeRequest(lambda: self.service.drafts_store.pop(kwargs["id"], None))


@pytest.fixture
def mail(tmp_path):
    path = tmp_path / "mail.sqlite"
    initialize_database(path)
    service = FakeGmail()
    provider = GmailProvider(service=service, expected_mailbox=service.account, write_enabled=True)
    class Imported:
        def fetch_new_emails(self):
            return [{"provider": "gmail", "remote_message_id": "m1", "remote_thread_id": "t1", "source_mailbox": service.account,
                "from_name": "Customer", "from_email": "customer@example.invalid", "subject": "Question", "body": "Hello"}]
    import_emails(path, Imported())
    with open_database(path) as connection:
        email_id = connection.execute("SELECT id FROM emails").fetchone()["id"]
    draft_id = create_draft_reply(path, email_id, "Approved reply")
    config = AppConfig(RunMode.LIVE, "gmail", test_mailbox=service.account, gmail_write_enabled=True,
        enable_sending=True, send_allowlist=("customer@example.invalid",))
    return SimpleNamespace(path=path, provider=provider, service=service, config=config, draft_id=draft_id, email_id=email_id)


def perform(mail, send=False):
    approved = delivery.snapshot(mail.path, mail.draft_id, mail.config)
    return delivery.deliver(mail.path, mail.draft_id, approved["content_hash"], mail.config, mail.provider, send=send)


def test_create_then_send_exact_content_and_mark_complete(mail):
    assert perform(mail)["status"] == "drafted"
    assert mail.service.send_calls == 0
    assert perform(mail, True)["status"] == "sent"
    assert mail.service.send_calls == 1 and get_draft(mail.path, mail.draft_id).status == "approved"
    raw = base64.urlsafe_b64decode(mail.service.raw_sent)
    message = BytesParser(policy=policy.default).parsebytes(raw)
    assert message["From"] == mail.service.account and message["To"] == "customer@example.invalid"
    assert message.get("Cc") is None and message.get("Bcc") is None
    assert message.get_content().strip() == "Approved reply"
    assert message["In-Reply-To"] == "<incoming@example.invalid>"
    with pytest.raises(ValueError):
        perform(mail, True)
    assert mail.service.send_calls == 1


@pytest.mark.parametrize("mode", [RunMode.MOCK, RunMode.DRY_RUN])
def test_modes_cannot_send(mail, mode):
    mail.config = replace(mail.config, mode=mode)
    with pytest.raises(ValueError):
        perform(mail, True)
    assert mail.service.send_calls == 0


def test_sender_allowlist_pause_and_source_mailbox_boundaries(mail):
    perform(mail)
    denied = replace(mail.config, send_allowlist=("owner@example.invalid",))
    with pytest.raises(ValueError, match="allowed"):
        delivery.deliver(mail.path, mail.draft_id, delivery.snapshot(mail.path, mail.draft_id, denied)["content_hash"], denied, mail.provider, send=True)
    set_mail_paused(mail.path, True)
    with pytest.raises(ValueError, match="paused"):
        perform(mail, True)
    set_mail_paused(mail.path, False)
    with open_database(mail.path) as connection:
        connection.execute("UPDATE emails SET source_mailbox='another@example.invalid'")
    with pytest.raises(ValueError, match="Re-import"):
        perform(mail, True)
    assert mail.service.send_calls == 0


def test_changed_body_and_unsaved_ui_text_require_new_approval(mail):
    original = delivery.snapshot(mail.path, mail.draft_id, mail.config)
    with pytest.raises(ValueError, match="Save your"):
        delivery.snapshot(mail.path, mail.draft_id, mail.config, "Unsaved change")
    edit_draft(mail.path, mail.draft_id, "Changed")
    with pytest.raises(ValueError, match="changed"):
        delivery.deliver(mail.path, mail.draft_id, original["content_hash"], mail.config, mail.provider)
    assert mail.service.create_calls == 0


def test_staged_reply_is_locked_until_remote_draft_is_cancelled(mail):
    perform(mail)
    with pytest.raises(ValueError, match="Cancel"):
        edit_draft(mail.path, mail.draft_id, "Changed")
    delivery.cancel_gmail_draft(mail.path, mail.draft_id, mail.config, mail.provider)
    edit_draft(mail.path, mail.draft_id, "Changed")
    assert get_draft(mail.path, mail.draft_id).body == "Changed"


def test_timeout_after_send_is_reconciled_without_resending(mail):
    perform(mail)
    mail.service.uncertain_send = True
    with pytest.raises(DeliveryUncertain) as caught:
        perform(mail, True)
    assert "RAW SECRET" not in str(caught.value)
    assert delivery.outbox_for_draft(mail.path, mail.draft_id)["status"] == "unknown"
    with pytest.raises(ValueError):
        perform(mail, True)
    result = delivery.reconcile(mail.path, mail.draft_id, mail.config, mail.provider)
    assert result["status"] == "sent" and mail.service.send_calls == 1


def test_crash_after_remote_create_does_not_create_a_duplicate(mail):
    mail.service.crash_create = True
    with pytest.raises(Crash):
        perform(mail)
    with pytest.raises(ValueError):
        perform(mail)
    assert delivery.reconcile(mail.path, mail.draft_id, mail.config, mail.provider)["status"] == "drafted"
    assert mail.service.create_calls == 1


def test_definite_rejection_preserves_the_staged_draft(mail):
    perform(mail)
    mail.service.reject_send = True
    with pytest.raises(DeliveryRejected) as caught:
        perform(mail, True)
    assert "SECRET" not in str(caught.value)
    assert delivery.outbox_for_draft(mail.path, mail.draft_id)["status"] == "drafted"
    assert get_draft(mail.path, mail.draft_id).status == "pending"


def test_two_send_attempts_issue_one_external_send(mail):
    perform(mail)
    def attempt(_):
        try:
            return perform(mail, True)["status"]
        except ValueError:
            return "blocked"
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, range(2)))
    assert sorted(results) == ["blocked", "sent"]
    assert mail.service.send_calls == 1


def test_wrong_authenticated_account_never_creates_or_sends(mail):
    mail.service.account = "wrong@example.invalid"
    with pytest.raises(DeliveryRejected):
        perform(mail)
    assert mail.service.send_calls == 0 and mail.service.create_calls == 0


def test_mailbox_scoping_and_legacy_binding_on_reimport(mail):
    with open_database(mail.path) as connection:
        connection.execute("UPDATE emails SET source_mailbox=NULL")
    with pytest.raises(ValueError, match="Re-import"):
        delivery.snapshot(mail.path, mail.draft_id, mail.config)
    class Imported:
        def fetch_new_emails(self):
            return [{"provider": "gmail", "remote_message_id": "m1", "source_mailbox": "owner@example.invalid",
                "from_name": "Customer", "from_email": "customer@example.invalid", "subject": "Question", "body": "Hello"}]
    assert import_emails(mail.path, Imported()) == 0
    assert delivery.snapshot(mail.path, mail.draft_id, mail.config)["recipient"] == "customer@example.invalid"


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def wait_task(qapp, owner, attribute):
    end = time.monotonic() + 5
    while getattr(owner, attribute) is not None and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    assert getattr(owner, attribute) is None


def test_review_ui_requires_confirmation_for_draft_and_send(mail, qapp, monkeypatch):
    from app.ui.approval import ApprovalQueueWidget
    widget = ApprovalQueueWidget(mail.path, provider=mail.provider)
    widget.set_connection(mail.config, mail.provider)
    monkeypatch.setattr(widget, "_confirmed", lambda *args, **kwargs: False)
    widget.create_gmail_draft()
    assert mail.service.create_calls == 0
    monkeypatch.setattr(widget, "_confirmed", lambda *args, **kwargs: True)
    widget.create_gmail_draft()
    wait_task(qapp, widget, "delivery_task")
    assert mail.service.create_calls == 1 and widget.approve_button.isEnabled()
    widget.approve()
    wait_task(qapp, widget, "delivery_task")
    assert mail.service.send_calls == 1 and widget.draft_list.count() == 0
    widget.close()


def test_connection_ui_switches_runtime_provider_and_saves_only_metadata(mail, qapp, tmp_path, monkeypatch):
    client = tmp_path / "client.json"
    client.write_text("{}")
    window = MainWindow(mail.path, config=AppConfig(RunMode.MOCK, "mock"))
    control = window.settings_widget.gmail_widget
    control.account_input.setText("owner@example.invalid")
    control.credentials_input.setText(str(client))
    control.send_checkbox.setChecked(True)
    control.recipients_input.setText("customer@example.invalid")
    monkeypatch.setattr("app.ui.gmail_connection.email_provider", lambda config: mail.provider)
    control.connect_gmail()
    wait_task(qapp, control, "task")
    assert window.inbox_widget.provider is mail.provider
    assert window.config.sending_enabled is True
    assert window.inbox_widget.import_button.text() == "Import from Gmail"
    stored = load_connection(mail.path)
    assert stored["MINI_CRM_PROVIDER"] == "gmail"
    assert not any("TOKEN" in name or "SECRET" in name for name in stored)
    window.close()


@pytest.mark.parametrize("outcome", ["accepted", "timeout", "rejected", "redirect", "delete_missing"])
def test_real_mutation_transport_never_replays_or_redirects(monkeypatch, outcome):
    """Exercise production HTTP code using HTTPX's in-memory transport."""
    import httpx
    calls = []
    real_client = httpx.Client
    def handler(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer fake-test-token"
        if outcome == "timeout":
            raise httpx.ReadTimeout("PRIVATE ERROR", request=request)
        if outcome == "rejected":
            return httpx.Response(403, json={"error": "PRIVATE ERROR"})
        if outcome == "redirect":
            return httpx.Response(307, headers={"Location": "https://example.invalid/leak"})
        if outcome == "delete_missing":
            return httpx.Response(404)
        return httpx.Response(200, json={"id": "sent-1"})
    def client(**options):
        assert options["trust_env"] is False and options["follow_redirects"] is False
        options["transport"].close()
        options["transport"] = httpx.MockTransport(handler)
        return real_client(**options)
    monkeypatch.setattr(httpx, "Client", client)
    request = SimpleNamespace(uri="https://gmail.googleapis.com/gmail/v1/users/me/drafts/send",
        method="POST", body='{"id":"draft-1"}', headers={"Content-Type": "application/json"},
        http=SimpleNamespace(credentials=SimpleNamespace(valid=True, token="fake-test-token")))
    provider = GmailProvider()
    if outcome in {"accepted", "delete_missing"}:
        assert provider._mutation(request, delete=outcome == "delete_missing") == ({"id": "sent-1"} if outcome == "accepted" else {})
    else:
        with pytest.raises(DeliveryRejected if outcome == "rejected" else DeliveryUncertain) as error:
            provider._mutation(request)
        assert "PRIVATE" not in str(error.value)
    assert len(calls) == 1


def test_write_permissions_require_new_google_consent(tmp_path, monkeypatch):
    import json
    from google.oauth2 import credentials as credentials_module
    from app.providers.gmail import GMAIL_READONLY_SCOPE, GMAIL_COMPOSE_SCOPE
    from app.config import ConfigurationError
    client = tmp_path / "oauth.json"
    client.write_text("{}")
    calls = []
    class Keyring:
        value = json.dumps({"scopes": [GMAIL_READONLY_SCOPE]})
        def get_password(self, *args):
            return self.value
        def set_password(self, service, user, value):
            self.value = value
    class Credentials:
        valid = True
        def has_scopes(self, scopes):
            return set(scopes) == {GMAIL_READONLY_SCOPE, GMAIL_COMPOSE_SCOPE}
        def to_json(self):
            return json.dumps({"scopes": [GMAIL_READONLY_SCOPE, GMAIL_COMPOSE_SCOPE]})
    def unexpected_readonly_reuse(*args):
        raise AssertionError("A readonly token cannot authorize compose")
    monkeypatch.setattr(credentials_module.Credentials, "from_authorized_user_info", unexpected_readonly_reuse)
    class Flow:
        def run_local_server(self, **options):
            assert options["prompt"] == "consent" and options["access_type"] == "offline"
            calls.append(options)
            return Credentials()
    def factory(path, scopes):
        assert set(scopes) == {GMAIL_READONLY_SCOPE, GMAIL_COMPOSE_SCOPE}
        return Flow()
    provider = GmailProvider(client, keyring_backend=Keyring(), flow_factory=factory,
        service_builder=lambda *args, **kwargs: "fake-service", write_enabled=True)
    with pytest.raises(ConfigurationError, match="Reconnect"):
        provider._service_or_login(allow_login=False)
    assert not calls
    assert provider._service_or_login() == "fake-service"
    assert len(calls) == 1


def test_demo_cannot_enable_live_sending(mail, qapp, monkeypatch):
    window = MainWindow(mail.path, email_provider=mail.provider, config=mail.config, demo_mode=True)
    monkeypatch.setattr(window.approval_widget, "_confirmed", lambda *args: True)
    window.approval_widget.create_gmail_draft()
    window.approval_widget.approve()
    window.settings_widget.gmail_widget.connect_gmail()
    assert not window.approval_widget.sending_enabled
    assert mail.service.create_calls == mail.service.send_calls == 0
    window.close()
