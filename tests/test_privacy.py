"""Focused offline tests for the M10 privacy boundary."""

import json

import pytest

from app.privacy import PrivacyStatus, mask_text, masked_preview, restore_text
from app.providers.llm_base import LLMProvider
from app.providers.llm_masked import MaskedLLMProvider


def test_cards_ibans_and_configured_ids_are_masked():
    text = "card 4111 1111 1111 1111, iban GB82WEST12345698765432, id ZX-123456"
    result = mask_text(text, [r"\bZX-\d{6}\b"])
    assert result.status is PrivacyStatus.OK
    assert "4111" not in result.masked_text
    assert "GB82" not in result.masked_text
    assert "ZX-123456" not in result.masked_text
    assert result.masked_text.count("[[PERSONAL_DATA_") == 3


def test_provider_sees_masked_text_and_safe_response_is_restored():
    seen = []

    class Provider(LLMProvider):
        accepts_customer_data = True

        def complete(self, system, prompt, json_mode=False):
            seen.append(system + prompt)
            return json.dumps({"text": prompt})

        def embed(self, text):
            seen.append(text)
            return [1.0]

    source = "card 4111111111111111"
    result = MaskedLLMProvider(Provider()).complete("system", source, True)
    assert all(source not in value for value in seen)
    assert source in result


def test_unknown_duplicate_and_malformed_placeholders_fail_closed():
    masking = mask_text("ID ZX-123456", [r"\bZX-\d{6}\b"])
    placeholder = masking.replacements[0][0]
    assert restore_text(placeholder + " " + placeholder, masking).status is PrivacyStatus.FAILED
    assert restore_text("[[PERSONAL_DATA_999]]", masking).status is PrivacyStatus.FAILED
    assert restore_text("[[PERSONAL_DATA_1]]", masking).status is PrivacyStatus.FAILED


def test_preview_contains_no_original_value():
    source = "card 4111111111111111"
    system, prompt = masked_preview("", source)
    assert source not in system + prompt
    assert "[[PERSONAL_DATA_001]]" in prompt
