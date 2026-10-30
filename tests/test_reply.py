"""Focused safety tests for the Milestone 4 reply pipeline."""

import json
from pathlib import Path

import pytest

from app.db.database import initialize_database, open_database
from app.db.drafts import list_drafts
from app.db.emails import EmailRecord
from app.db.faqs import FAQEntry, create_faq
from app.reply.matcher import DEFAULT_THRESHOLD, find_best_faq
from app.reply.pipeline import process_email
from app.providers.llm_fake import FakeLLMProvider
from app.providers.llm_cloud import CloudLLMProvider


def _email(subject: str, body: str) -> EmailRecord:
    return EmailRecord(1, None, "one", "Ada", "a@example.com", subject, body, None)


def _database(path: Path, approved: bool = True) -> Path:
    initialize_database(path)
    create_faq(path, "What are your opening hours?", "We are open on weekdays.", approved)
    with open_database(path) as connection:
        connection.execute(
            "INSERT INTO emails (id, from_name, from_email, subject, body) "
            "VALUES (1, 'Ada', 'a@example.com', 'Opening hours', 'When are you open?')"
        )
    return path


def test_sample_wording_is_calibrated_without_matching_unrelated_text() -> None:
    """The real fixture wording should use the normal threshold."""
    root = Path(__file__).parents[1]
    email_data = json.loads((root / "sample_data" / "emails.json").read_text())
    faq_data = json.loads((root / "sample_data" / "faqs.json").read_text())
    faqs = [
        FAQEntry(item["id"], item["question"], item["answer"], True, "")
        for item in faq_data
    ]
    provider = FakeLLMProvider()

    for email_id, expected_faq_id in ((1, 1), (2, 2), (5, 3)):
        item = next(item for item in email_data if item["id"] == email_id)
        result = find_best_faq(
            _email(item["subject"], item["body"]), faqs, provider
        )
        assert result.faq is not None
        assert result.faq.id == expected_faq_id
        assert result.score > DEFAULT_THRESHOLD

    unrelated = find_best_faq(
        _email("Unrelated", "Can you change my account?"), faqs, provider
    )
    assert unrelated.score < DEFAULT_THRESHOLD


def test_safe_match_creates_pending_draft_from_approved_faq(tmp_path: Path) -> None:
    path = _database(tmp_path / "reply.db")
    result = process_email(_email("Opening hours", "When are you open?"),
                           path, FakeLLMProvider())

    assert result.status == "pending"
    assert result.draft_id is not None
    assert list_drafts(path)[0].status == "pending"
    assert "We are open on weekdays." in result.body


def test_low_confidence_escalates_without_a_draft(tmp_path: Path) -> None:
    path = _database(tmp_path / "reply.db")
    result = process_email(_email("Unrelated", "Can you change my account?"),
                           path, FakeLLMProvider())

    assert result.status == "escalated"
    assert list_drafts(path) == []


def test_cloud_customer_data_is_fail_closed_before_opener(tmp_path: Path, monkeypatch) -> None:
    path = _database(tmp_path / "reply.db")
    calls = []
    monkeypatch.setenv("KEY", "secret")
    provider = CloudLLMProvider(
        "https://example.invalid", "m", "KEY",
        opener=lambda request, timeout: calls.append(request),
    )

    result = process_email(_email("Opening hours", "When are you open?"), path, provider)

    assert result.status == "escalated"
    assert "before M10" in (result.reason or "")
    assert calls == []
    assert list_drafts(path) == []


class EmbeddingUnavailableProvider(FakeLLMProvider):
    def embed(self, text: str) -> list[float]:
        raise NotImplementedError("embedding unavailable")


def test_embedding_limitation_escalates_without_ui_exception(tmp_path: Path) -> None:
    path = _database(tmp_path / "reply.db")

    result = process_email(
        _email("Opening hours", "When are you open?"),
        path,
        EmbeddingUnavailableProvider(),
    )

    assert result.status == "escalated"
    assert "embedding" in (result.reason or "")
    assert list_drafts(path) == []


class CallbackProvider(FakeLLMProvider):
    def __init__(self, callback):
        self.audit_callback = callback

    def complete(self, system: str, prompt: str, json_mode: bool = False) -> str:
        self.audit_callback({
            "provider": "fake", "model": "offline", "prompt_version": "reply_v1",
            "faq_id": 1, "prompt_tokens": 1, "completion_tokens": 1,
            "total_tokens": 2, "cost": None,
        })
        return super().complete(system, prompt, json_mode)


def test_provider_callback_produces_one_complete_audit_record(tmp_path: Path) -> None:
    path = _database(tmp_path / "reply.db")
    records = []
    result = process_email(
        _email("Opening hours", "When are you open?"),
        path,
        CallbackProvider(records.append),
        audit_callback=records.append,
    )

    assert result.status == "pending"
    assert len(records) == 1
    assert records[0]["faq_id"] == 1
    assert "When are you open?" not in repr(records)


@pytest.mark.parametrize("text", [
    "I need a refund.",
    "I will sue you; this is legal.",
    "Your service is unacceptable and I am angry.",
    "What does it cost? It is $20.",
])
def test_high_risk_categories_escalate_before_provider(tmp_path: Path, text: str) -> None:
    path = _database(tmp_path / "reply.db")
    result = process_email(_email("Question", text), path, FakeLLMProvider())
    assert result.status == "escalated"
    assert list_drafts(path) == []


def test_unapproved_faq_is_not_used(tmp_path: Path) -> None:
    path = _database(tmp_path / "reply.db", approved=False)
    result = process_email(_email("Opening hours", "When are you open?"),
                           path, FakeLLMProvider())
    assert result.status == "escalated"
    assert list_drafts(path) == []


class InvalidProvider(FakeLLMProvider):
    def complete(self, system: str, prompt: str, json_mode: bool = False) -> str:
        return '{"faq_id": 999, "reply": "invented answer"}'


def test_invalid_provider_output_is_not_saved(tmp_path: Path) -> None:
    path = _database(tmp_path / "reply.db")
    result = process_email(_email("Opening hours", "When are you open?"),
                           path, InvalidProvider())
    assert result.status == "escalated"
    assert list_drafts(path) == []


class RecordingProvider(FakeLLMProvider):
    def __init__(self, response: str | None = None) -> None:
        self.calls: list[str] = []
        self.response = response

    def embed(self, text: str) -> list[float]:
        self.calls.append("embed")
        return super().embed(text)

    def complete(self, system: str, prompt: str, json_mode: bool = False) -> str:
        self.calls.append("complete")
        if self.response is not None:
            return self.response
        return super().complete(system, prompt, json_mode)


@pytest.mark.parametrize("text", ["?", "...!!!", "12345"])
def test_unclear_message_makes_no_provider_calls(tmp_path: Path, text: str) -> None:
    path = _database(tmp_path / "reply.db")
    provider = RecordingProvider()

    result = process_email(_email(text, ""), path, provider)

    assert result.status == "escalated"
    assert provider.calls == []


def test_invented_prose_after_approved_answer_is_rejected(tmp_path: Path) -> None:
    path = _database(tmp_path / "reply.db")
    response = (
        '{"faq_id": 1, "reply": "Hello Ada,\\n'
        'We are open on weekdays. And we also provide premium consulting."}'
    )
    provider = RecordingProvider(response)

    result = process_email(_email("Opening hours", "When are you open?"),
                           path, provider)

    assert result.status == "escalated"
    assert list_drafts(path) == []
