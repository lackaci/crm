import json
from urllib.error import URLError

import pytest

from app.providers.llm_cloud import CloudLLMError, CloudLLMProvider
from app.providers.llm_masked import MaskedLLMProvider


class Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.value).encode()


def test_cloud_request_uses_secret_header_without_leaking_secret(monkeypatch):
    seen = []
    records = []

    def opener(request, timeout):
        seen.append((request, timeout))
        return Response({"choices": [{"message": {"content": '{"ok": true}'}}]})

    monkeypatch.setenv("TEST_LLM_SECRET", "test-secret")
    provider = CloudLLMProvider("https://example.invalid", "test-model",
                                "TEST_LLM_SECRET", opener=opener,
                                audit_callback=records.append)
    assert provider.complete_masked("system", "user", json_mode=True) == '{"ok": true}'
    request, timeout = seen[0]
    assert timeout == 10
    assert json.loads(request.data)["model"] == "test-model"
    assert request.get_header("Authorization") == "Bearer test-secret"
    assert "test-secret" not in repr(request)
    assert records
    assert "test-secret" not in repr(records)

    output = provider.complete_masked("system", "user", json_mode=True)
    assert "test-secret" not in repr(output)


def test_cloud_error_and_audit_repr_do_not_contain_secret(monkeypatch):
    monkeypatch.setenv("TEST_LLM_SECRET", "test-secret")
    provider = CloudLLMProvider(
        "https://example.invalid", "test-model", "TEST_LLM_SECRET",
        opener=lambda request, timeout: Response({"choices": []}),
    )
    with pytest.raises(CloudLLMError) as raised:
        provider.complete_masked("system", "user", json_mode=True)
    assert "test-secret" not in repr(raised.value)
    assert "test-secret" not in repr(raised.value)


def test_cloud_retries_are_bounded(monkeypatch):
    calls = []

    def opener(request, timeout):
        calls.append(timeout)
        raise URLError("offline")

    monkeypatch.setenv("KEY", "secret")
    provider = CloudLLMProvider("https://example.invalid", "m", "KEY",
                                retries=2, opener=opener)
    with pytest.raises(CloudLLMError, match="bounded retries"):
        provider.complete_masked("s", "p")
    assert len(calls) == 3


def test_cloud_rejects_malformed_structured_response(monkeypatch):
    monkeypatch.setenv("KEY", "secret")
    provider = CloudLLMProvider(
        "https://example.invalid", "m", "KEY",
        opener=lambda request, timeout: Response({"choices": []}),
    )
    with pytest.raises(CloudLLMError, match="malformed"):
        provider.complete_masked("s", "p", json_mode=True)


@pytest.mark.parametrize("endpoint", [
    "http://example.invalid",
    "not a url",
    "https://user:pass@example.invalid",
    "https://example.invalid:bad",
    "https://example.invalid:0",
])
def test_cloud_rejects_unsafe_endpoints(endpoint):
    with pytest.raises(ValueError, match="HTTPS"):
        CloudLLMProvider(endpoint, "m")


def test_cloud_does_not_open_for_customer_context(monkeypatch):
    calls = []
    monkeypatch.setenv("KEY", "secret")
    provider = CloudLLMProvider(
        "https://example.invalid", "m", "KEY",
        opener=lambda request, timeout: calls.append(request),
    )
    with pytest.raises(RuntimeError, match="masking boundary"):
        provider.complete("system", "CUSTOMER_NAME: Ada\nEMAIL_CONTEXT: private")
    assert calls == []


def test_masked_boundary_sends_placeholders_once_and_restores_response(monkeypatch):
    seen = []
    sensitive = "4111 1111 1111 1111"

    def opener(request, timeout):
        payload = json.loads(request.data)
        seen.append(payload)
        return Response({
            "choices": [{
                "message": {
                    "content": '{"reply": "Reference [[PERSONAL_DATA_001]]"}'
                }
            }]
        })

    monkeypatch.setenv("KEY", "secret")
    cloud = CloudLLMProvider(
        "https://example.invalid", "m", "KEY", opener=opener
    )
    result = MaskedLLMProvider(cloud).complete(
        "system",
        f"CUSTOMER_NAME: Ada\nEMAIL_CONTEXT: card {sensitive}",
        json_mode=True,
    )

    request_text = json.dumps(seen[0])
    assert sensitive not in request_text
    assert "[[PERSONAL_DATA_001]]" in request_text
    assert sensitive in result


def test_cloud_audit_contains_metadata_not_prompt(monkeypatch):
    records = []
    monkeypatch.setenv("KEY", "secret")
    provider = CloudLLMProvider(
        "https://example.invalid", "m", "KEY",
        opener=lambda request, timeout: Response({
            "choices": [{"message": {"content": '{"ok": true}'}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3,
                       "total_tokens": 5, "cost": 0.01},
        }),
        audit_callback=records.append,
    )
    provider.complete_masked("s", "FAQ_ID: 7\nprivate customer body", json_mode=True)
    assert records == [{
        "provider": "cloud", "model": "m", "prompt_version": "reply_v1",
        "faq_id": 7, "outcome": "success", "error_category": None,
        "error_type": None, "retry_count": 0, "prompt_tokens": 2,
        "completion_tokens": 3, "total_tokens": 5, "cost": 0.01,
    }]
    assert "private customer body" not in repr(records)


def test_cloud_transport_exhaustion_has_one_sanitized_failure_audit(monkeypatch):
    records = []
    calls = []

    def opener(request, timeout):
        calls.append(request)
        raise URLError("this message must not be audited")

    monkeypatch.setenv("KEY", "secret")
    provider = CloudLLMProvider(
        "https://example.invalid", "m", "KEY", retries=2, opener=opener,
        audit_callback=records.append, prompt_version="v2",
    )
    with pytest.raises(CloudLLMError, match="bounded retries"):
        provider.complete_masked("system", "FAQ_ID: 12\nprivate prompt", json_mode=True)

    assert len(calls) == 3
    assert records == [{
        "provider": "cloud", "model": "m", "prompt_version": "v2",
        "faq_id": 12, "outcome": "failure",
        "error_category": "transport", "error_type": "URLError",
        "retry_count": 2, "prompt_tokens": None, "completion_tokens": None,
        "total_tokens": None, "cost": None,
    }]
    assert "private prompt" not in repr(records)
    assert "this message" not in repr(records)


def test_cloud_malformed_response_has_one_failure_audit(monkeypatch):
    records = []
    monkeypatch.setenv("KEY", "secret")
    provider = CloudLLMProvider(
        "https://example.invalid", "m", "KEY",
        opener=lambda request, timeout: Response({"choices": []}),
        audit_callback=records.append,
    )
    with pytest.raises(CloudLLMError, match="malformed"):
        provider.complete_masked("system", "FAQ_ID: 9\nprivate prompt", json_mode=True)

    assert len(records) == 1
    assert records[0]["provider"] == "cloud"
    assert records[0]["model"] == "m"
    assert records[0]["prompt_version"] == "reply_v1"
    assert records[0]["faq_id"] == 9
    assert records[0]["outcome"] == "failure"
    assert records[0]["error_category"] == "response"
    assert records[0]["error_type"] == "malformed_response"
    assert records[0]["retry_count"] == 0
    assert "private prompt" not in repr(records)


def test_cloud_direct_calls_fail_closed_before_key_lookup_or_transport(monkeypatch):
    records = []
    monkeypatch.delenv("KEY", raising=False)
    provider = CloudLLMProvider(
        "https://example.invalid", "m", "KEY", audit_callback=records.append,
    )
    with pytest.raises(RuntimeError, match="masking boundary"):
        provider.complete("system", "prompt")
    assert records == []

    monkeypatch.setenv("KEY", "secret")
    with pytest.raises(RuntimeError, match="masking boundary"):
        provider.complete("system", "CUSTOMER_NAME: private")
    assert records == []


def test_masked_entry_point_rejects_unmasked_sensitive_value(monkeypatch):
    calls = []
    monkeypatch.setenv("KEY", "secret")
    provider = CloudLLMProvider(
        "https://example.invalid", "m", "KEY",
        opener=lambda request, timeout: calls.append(request),
    )
    with pytest.raises(RuntimeError, match="already-masked"):
        provider.complete_masked("system", "card 4111 1111 1111 1111")
    assert calls == []
