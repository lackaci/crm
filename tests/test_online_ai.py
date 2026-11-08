"""Online integrations use an in-memory HTTP transport, never paid APIs."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace

import httpx
import pytest
from PySide6.QtWidgets import QApplication

from app.config import AppConfig, RunMode
from app.db import documents as store
from app.db.customers import add_customer
from app.db.database import initialize_database, open_database
from app.db.drafts import list_drafts
from app.db.emails import import_emails, get_email
from app.db.faqs import create_faq, update_faq, approve_faq
from app.db.files import store_file
from app.document_reader import DocumentPage
from app.documents import process_documents
from app.providers.document_factory import OnlineDocumentExtractor, document_provider
from app.providers.document_model import DemoDocumentExtractor, DocumentError, FIELD_NAMES, EXTRACTION_SCHEMA
from app.providers.llm_base import CustomerDataNotAllowedError
from app.providers.llm_masked import MaskedLLMProvider
from app.providers.llm_online import OnlineFAQProvider
from app.providers.online_model import OnlineClient, OnlineSettings, OnlineSetupError, OPENAI_ENDPOINT
from app.providers.online_model import VERCEL_ENDPOINT, VERCEL_MODEL, VERCEL_EMBEDDING_MODEL, VERCEL_KEY_ENV
from app.reply.pipeline import process_email
from app.ui.main_window import MainWindow
from app.ui.documents import DocumentsWidget

ANSWER = "We are open weekdays."


def wire_document():
    value = DemoDocumentExtractor().extract_page(DocumentPage(1, "DEMO-CONTRACT-001"))
    rows = value["fields"]
    value["fields"] = {name: next(({"value": row["value"], "evidence": row["evidence"]} for row in rows if row["name"] == name), None) for name in FIELD_NAMES}
    return value


def completed(value):
    return {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(value)}]}]}


@pytest.fixture
def api(monkeypatch):
    import app.providers.online_model as online
    calls = []
    handler = {"override": None}
    real_client = httpx.Client
    monkeypatch.setattr(online, "api_key", lambda settings: "fake-unit-api-key")
    def route(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer fake-unit-api-key"
        if handler["override"]:
            return handler["override"](request)
        body = json.loads(request.content)
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"embedding": [1.0, 0.0]}]})
        schema = body.get("text", {}).get("format", {}).get("schema", body.get("response_format", {}).get("json_schema", {}).get("schema", {}))
        value = {"faq_id": 1, "reply": ANSWER} if "faq_id" in schema.get("properties", {}) else wire_document()
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]})
        return httpx.Response(200, json=completed(value))
    def client(**options):
        assert options["trust_env"] is False and options["follow_redirects"] is False
        options["transport"].close()
        options["transport"] = httpx.MockTransport(route)
        return real_client(**options)
    monkeypatch.setattr(httpx, "Client", client)
    return SimpleNamespace(calls=calls, handler=handler)


@pytest.fixture
def workspace(tmp_path):
    path, storage = tmp_path / "crm.sqlite", tmp_path / "files"
    initialize_database(path)
    customer = add_customer(path, "Customer", "customer@example.invalid")
    create_faq(path, "What are your opening hours?", ANSWER, approved=True)
    class Mail:
        def fetch_new_emails(self):
            return [{"id": "mail-1", "from_name": "Customer", "from_email": "customer@example.invalid",
                "subject": "Opening hours?", "body": "When are you open? Reply to customer@example.invalid"}]
    import_emails(path, Mail())
    return SimpleNamespace(path=path, storage=storage, customer=customer)


def test_openai_documents_use_images_strict_schema_and_no_storage(api):
    provider = OnlineDocumentExtractor()
    assert provider.resolve().startswith("online-profile:")
    result = provider.extract_page(DocumentPage(1, "Contract text", b"jpeg-image"))
    assert result["document_type"] == "contract"
    assert len(api.calls) == 1 and str(api.calls[0].url) == OPENAI_ENDPOINT
    payload = json.loads(api.calls[0].content)
    assert payload["store"] is False and payload["stream"] is False
    assert payload["text"]["format"]["schema"] == EXTRACTION_SCHEMA
    assert payload["text"]["format"]["strict"] is True
    assert payload["input"][0]["content"][1]["image_url"].startswith("data:image/jpeg;base64,")
    assert "tools" not in payload and "untrusted DATA" in payload["instructions"]


def test_compatible_api_uses_chat_format(api):
    settings = OnlineSettings("vision-model", "compatible", "https://provider.test/v1/chat/completions", "PROVIDER_KEY")
    result = OnlineDocumentExtractor(settings).extract_page(DocumentPage(1, "text", b"jpeg"))
    assert result["document_type"] == "contract"
    payload = json.loads(api.calls[0].content)
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert payload["messages"][1]["content"][1]["type"] == "image_url"


def test_vercel_documents_and_faq_use_gateway_models_and_endpoints(workspace, api):
    settings = OnlineSettings.vercel()
    result = OnlineDocumentExtractor(settings).extract_page(DocumentPage(1, "Contract text", b"jpeg"))
    assert result["document_type"] == "contract"
    assert str(api.calls[0].url) == VERCEL_ENDPOINT
    payload = json.loads(api.calls[0].content)
    assert payload["model"] == VERCEL_MODEL
    assert payload["response_format"]["json_schema"]["schema"] == EXTRACTION_SCHEMA
    assert payload["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    result = process_email(get_email(workspace.path, 1), workspace.path, OnlineFAQProvider(settings))
    assert result.status == "pending" and result.body == ANSWER
    embeddings = [request for request in api.calls if request.url.path.endswith("/embeddings")]
    assert embeddings
    assert all(str(request.url) == "https://ai-gateway.vercel.sh/v1/embeddings" for request in embeddings)
    assert all(json.loads(request.content)["model"] == VERCEL_EMBEDDING_MODEL for request in embeddings)
    assert all(request.url.host == "ai-gateway.vercel.sh" for request in api.calls)
    assert "customer@example.invalid" not in "\n".join(request.content.decode() for request in api.calls)


@pytest.mark.parametrize("change", [
    {"endpoint": OPENAI_ENDPOINT}, {"model": "gpt-5.4-mini"}, {"embedding_model": "text-embedding-3-small"},
])
def test_vercel_rejects_wrong_endpoint_and_unprefixed_models(change):
    from dataclasses import replace
    with pytest.raises(DocumentError):
        replace(OnlineSettings.vercel(), **change)


def test_vercel_credentials_are_isolated_from_openai(monkeypatch):
    import keyring
    from app.providers.online_model import api_key
    monkeypatch.setattr(keyring, "get_password", lambda service, user: None)
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai-key")
    monkeypatch.delenv(VERCEL_KEY_ENV, raising=False)
    with pytest.raises(OnlineSetupError, match=VERCEL_KEY_ENV):
        api_key(OnlineSettings.vercel())
    monkeypatch.setenv(VERCEL_KEY_ENV, "fake-vercel-key")
    assert api_key(OnlineSettings.vercel()) == "fake-vercel-key"
    assert OnlineSettings.vercel().keyring_user != OnlineSettings().keyring_user


@pytest.mark.parametrize("status", [301, 400, 401, 403, 429, 503])
def test_http_failures_are_safe_and_never_retried(api, status):
    api.handler["override"] = lambda request: httpx.Response(status, json={"error": "PRIVATE API KEY AND DOCUMENT TEXT"}, headers={"Location": "https://other.test/leak"})
    with pytest.raises(DocumentError) as error:
        OnlineDocumentExtractor().extract_page(DocumentPage(1, "test"))
    assert "PRIVATE" not in str(error.value)
    assert len(api.calls) == 1


@pytest.mark.parametrize("value", [
    {"status": "incomplete", "output": []},
    {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]},
    {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "not JSON"}]}]},
    completed({"wrong": "structure"}),
])
def test_refusals_partial_and_invalid_data_are_not_saved(api, value):
    api.handler["override"] = lambda request: httpx.Response(200, json=value)
    with pytest.raises(DocumentError):
        OnlineDocumentExtractor().extract_page(DocumentPage(1, "text"))


def test_unauthorized_batch_stops_after_one_request(workspace, api, tmp_path):
    for index in range(2):
        source = tmp_path / f"contract-{index}.txt"
        source.write_text("Contract " + str(index))
        store_file(workspace.path, workspace.customer, source, "contract", workspace.storage)
    api.handler["override"] = lambda request: httpx.Response(401, json={"error": "PRIVATE"})
    with pytest.raises(OnlineSetupError):
        process_documents(workspace.path, workspace.storage, OnlineDocumentExtractor())
    assert len(api.calls) == 1


def test_online_cache_reuses_identical_documents(workspace, api, tmp_path):
    for filename in ("first.txt", "copy.txt"):
        source = tmp_path / filename
        source.write_text("Same fictional contract")
        store_file(workspace.path, workspace.customer, source, "contract", workspace.storage)
    provider = OnlineDocumentExtractor()
    result = process_documents(workspace.path, workspace.storage, provider)
    assert result.extracted == result.reused == 1 and len(api.calls) == 1
    assert process_documents(workspace.path, workspace.storage, provider).skipped == 2
    assert len(api.calls) == 1


def test_legacy_local_settings_are_preserved_on_migration(tmp_path):
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE desktop_document_settings(id INTEGER PRIMARY KEY,model TEXT DEFAULT 'qwen3-vl:2b-instruct',workbook_path TEXT,auto_extract INTEGER DEFAULT 0)")
        connection.execute("INSERT INTO desktop_document_settings(id,auto_extract) VALUES(1,1)")
    initialize_database(path)
    config = store.settings(path)
    assert config["provider"] == "local" and config["auto_extract"] == 1 and not config["online_replies"]
    initialize_database(path)
    assert store.settings(path)["provider"] == "local"


def test_key_is_saved_only_in_os_keyring(workspace, monkeypatch):
    import keyring
    from app.providers.online_model import save_api_key, api_key
    keys = {}
    monkeypatch.setattr(keyring, "set_password", lambda service, user, value: keys.update({(service,user): value}))
    monkeypatch.setattr(keyring, "get_password", lambda service, user: keys.get((service,user)))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = OnlineSettings()
    save_api_key(settings, "fake-private-key")
    store.save_settings(workspace.path, "qwen3-vl:2b-instruct", workspace.path.parent / "export.xlsx", provider="openai")
    assert api_key(settings) == "fake-private-key"
    assert "fake-private-key" not in json.dumps(store.settings(workspace.path))
    with closing(open_database(workspace.path)) as connection:
        assert all("fake-private-key" not in row["details"] for row in connection.execute("SELECT details FROM audit_log"))


def test_missing_key_does_not_start_a_network_request(api, monkeypatch):
    def missing(settings):
        raise OnlineSetupError("Missing test key")
    monkeypatch.setattr("app.providers.online_model.api_key", missing)
    with pytest.raises(OnlineSetupError):
        OnlineDocumentExtractor().resolve()
    assert not api.calls


def test_online_faq_uses_masked_text_and_approved_answer(workspace, api):
    provider = OnlineFAQProvider(OnlineSettings())
    result = process_email(get_email(workspace.path, 1), workspace.path, provider)
    assert result.status == "pending" and result.body == ANSWER
    assert len(list_drafts(workspace.path)) == 1
    assert any(str(request.url).endswith("/embeddings") for request in api.calls)
    assert "customer@example.invalid" not in "\n".join(request.content.decode() for request in api.calls)


def test_direct_unmasked_faq_calls_are_blocked(api):
    provider = OnlineFAQProvider(OnlineSettings())
    with pytest.raises(CustomerDataNotAllowedError):
        provider.complete("system", "raw customer text")
    with pytest.raises(CustomerDataNotAllowedError):
        provider.embed("Contact person@example.invalid")
    assert not api.calls


def test_risky_email_never_contacts_online_provider(workspace, api):
    with closing(open_database(workspace.path)) as connection, connection:
        connection.execute("UPDATE emails SET body='I demand a refund'")
    result = process_email(get_email(workspace.path, 1), workspace.path, OnlineFAQProvider(OnlineSettings()))
    assert result.status == "escalated" and not api.calls


def test_changed_reapproved_faq_cannot_commit_stale_reply(workspace, api):
    def change(request):
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"embedding": [1.0, 0.0]}]})
        update_faq(workspace.path, 1, "What are your opening hours?", "We are open weekends.")
        approve_faq(workspace.path, 1)
        return httpx.Response(200, json=completed({"faq_id": 1, "reply": ANSWER}))
    api.handler["override"] = change
    with pytest.raises(ValueError, match="changed"):
        process_email(get_email(workspace.path, 1), workspace.path, OnlineFAQProvider(OnlineSettings()))
    assert not list_drafts(workspace.path)


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def wait_task(qapp, owner, attribute):
    deadline = time.monotonic() + 8
    while getattr(owner, attribute) is not None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert getattr(owner, attribute) is None


@pytest.mark.parametrize("provider", ["openai", "vercel"])
def test_online_settings_apply_to_background_replies_and_preview(workspace, api, qapp, provider):
    window = MainWindow(workspace.path, storage_root=workspace.storage, config=AppConfig(RunMode.MOCK, "mock"))
    control = window.files_widget.documents_widget
    assert control.provider_combo.currentData() == "openai"
    control.provider_combo.setCurrentIndex(control.provider_combo.findData(provider))
    control.replies_checkbox.setChecked(True)
    assert control.save_settings()
    assert window.inbox_widget.llm_provider.uses_network
    window.inbox_widget.email_table.selectRow(0)
    window.inbox_widget.create_draft()
    assert window.inbox_widget.draft_task is not None
    wait_task(qapp, window.inbox_widget, "draft_task")
    assert len(list_drafts(workspace.path)) == 1
    window.faq_widget.sample_email_input.setPlainText("Opening hours?")
    window.faq_widget.test_sample()
    wait_task(qapp, window.faq_widget, "test_task")
    assert window.faq_widget.decision_label.text() == "Decision: Draft"
    assert len(list_drafts(workspace.path)) == 1
    control.provider_combo.setCurrentIndex(control.provider_combo.findData("local"))
    assert control.save_settings()
    assert not getattr(window.inbox_widget.llm_provider, "uses_network", False)
    window.close()


def test_vercel_settings_survive_local_switch_and_reload(workspace, api, qapp):
    widget = DocumentsWidget(workspace.path, workspace.storage)
    widget.provider_combo.setCurrentIndex(widget.provider_combo.findData("vercel"))
    assert widget.model_input.text() == VERCEL_MODEL
    assert widget.embedding_input.text() == VERCEL_EMBEDDING_MODEL
    assert widget.api_key_env_input.text() == VERCEL_KEY_ENV
    assert widget.endpoint_input.text() == VERCEL_ENDPOINT and widget.endpoint_input.isReadOnly()
    widget.replies_checkbox.setChecked(True)
    assert widget.save_settings()
    config = store.settings(workspace.path)
    assert config["provider"] == "vercel" and config["online_replies"]
    assert isinstance(document_provider(config), OnlineDocumentExtractor)
    widget.close()
    widget = DocumentsWidget(workspace.path, workspace.storage)
    assert widget.provider_combo.currentData() == "vercel" and widget.replies_checkbox.isChecked()
    widget.model_input.setText("anthropic/claude-sonnet-4.5")
    widget.provider_combo.setCurrentIndex(widget.provider_combo.findData("local"))
    assert widget.save_settings()
    widget.close()
    widget = DocumentsWidget(workspace.path, workspace.storage)
    widget.provider_combo.setCurrentIndex(widget.provider_combo.findData("vercel"))
    assert widget.model_input.text() == "anthropic/claude-sonnet-4.5"
    assert widget.api_key_env_input.text() == VERCEL_KEY_ENV
    assert widget.embedding_input.text() == VERCEL_EMBEDDING_MODEL
    assert widget.endpoint_input.text() == VERCEL_ENDPOINT
    assert not widget.replies_checkbox.isChecked()
    widget.close()


def test_demo_never_constructs_an_online_provider(workspace, api, qapp):
    store.save_settings(workspace.path, "qwen3-vl:2b-instruct", workspace.path.parent / "test.xlsx", provider="openai", online_replies=True)
    window = MainWindow(workspace.path, storage_root=workspace.storage, config=AppConfig(RunMode.MOCK, "mock"), demo_mode=True)
    control = window.files_widget.documents_widget
    assert not control.provider_combo.isEnabled()
    assert isinstance(document_provider(store.settings(workspace.path), demo_mode=True), DemoDocumentExtractor)
    assert not getattr(window.inbox_widget.llm_provider, "uses_network", False)
    assert not api.calls
    window.close()


def test_online_reply_failure_is_visible_beside_the_button_and_can_retry(workspace, api, qapp):
    window = MainWindow(workspace.path, storage_root=workspace.storage, config=AppConfig(RunMode.MOCK, "mock"))
    inbox = window.inbox_widget
    inbox.llm_provider = OnlineFAQProvider(OnlineSettings.vercel())
    inbox.email_table.selectRow(0)
    api.handler["override"] = lambda request: httpx.Response(401, json={"error": "PRIVATE-TOKEN"})
    inbox.create_draft_button.click()
    assert "Preparing a reply" in inbox.reply_status_label.text()
    wait_task(qapp, inbox, "draft_task")
    assert "authentication failed" in inbox.reply_status_label.text()
    assert "PRIVATE" not in inbox.reply_status_label.text()
    assert inbox.create_draft_button.isEnabled()
    assert not list_drafts(workspace.path)
    api.handler["override"] = None
    inbox.create_draft_button.click()
    wait_task(qapp, inbox, "draft_task")
    assert len(list_drafts(workspace.path)) == 1
    assert window.pages.currentWidget() is window.approval_widget
    window.close()


def test_saved_online_model_and_key_variable_survive_ui_reload(workspace, qapp):
    store.save_settings(workspace.path, "qwen3-vl:2b-instruct", workspace.path.parent / "test.xlsx", provider="openai",
        online_model="custom-online-model", api_key_env="LLM_API_KEY")
    widget = DocumentsWidget(workspace.path, workspace.storage)
    assert widget.model_input.text() == "custom-online-model"
    assert widget.api_key_env_input.text() == "LLM_API_KEY"
    widget.api_key_input.setText("not-to-be-carried-to-another-provider")
    widget.provider_combo.setCurrentIndex(widget.provider_combo.findData("local"))
    assert not widget.api_key_input.text()
    assert widget.save_settings()
    widget.close()
    restored = DocumentsWidget(workspace.path, workspace.storage)
    restored.provider_combo.setCurrentIndex(restored.provider_combo.findData("openai"))
    assert restored.model_input.text() == "custom-online-model"
    assert restored.api_key_env_input.text() == "LLM_API_KEY"
    restored.provider_combo.setCurrentIndex(restored.provider_combo.findData("compatible"))
    assert restored.api_key_env_input.text() == "COMPATIBLE_AI_API_KEY"
    restored.provider_combo.setCurrentIndex(restored.provider_combo.findData("openai"))
    assert restored.api_key_env_input.text() == "LLM_API_KEY"
    restored.close()
