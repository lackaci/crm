"""Focused tests for the offline M08 configuration loader."""

import pytest

from app.config import ConfigurationError, RunMode, load_config


def test_mock_is_default_and_has_no_required_settings():
    config = load_config({})
    assert config.mode is RunMode.MOCK
    assert config.provider == "mock"
    assert config.sending_enabled is False


def test_unknown_mode_is_rejected():
    with pytest.raises(ConfigurationError, match="exactly Mock"):
        load_config({"MINI_CRM_MODE": "Production"})


@pytest.mark.parametrize("mode", ["Dry-run", "Live"])
def test_non_mock_requires_settings(mode):
    with pytest.raises(ConfigurationError, match="MINI_CRM_TEST_MAILBOX"):
        load_config({"MINI_CRM_MODE": mode})


def test_provider_names_are_configuration_only():
    config = load_config({"MINI_CRM_PROVIDER": "cloud"})
    assert config.provider == "cloud"
    assert config.mode is RunMode.MOCK


def test_connection_endpoint_must_be_https():
    values = {
        "MINI_CRM_MODE": "Dry-run",
        "MINI_CRM_PROVIDER": "cloud",
        "MINI_CRM_TEST_MAILBOX": "test@example.invalid",
        "MINI_CRM_ENDPOINT": "http://example.invalid",
        "MINI_CRM_MODEL": "test-model",
        "MINI_CRM_API_KEY_ENV": "TEST_KEY",
    }
    with pytest.raises(ConfigurationError, match="HTTPS"):
        load_config(values)


def test_gmail_does_not_require_cloud_llm_settings(tmp_path):
    credentials = tmp_path / "client.json"
    credentials.write_text("{}", encoding="utf-8")
    config = load_config({
        "MINI_CRM_MODE": "Dry-run",
        "MINI_CRM_PROVIDER": "gmail",
        "MINI_CRM_TEST_MAILBOX": "crm-test@example.invalid",
        "MINI_CRM_GOOGLE_CREDENTIALS_FILE": str(credentials),
    })
    assert config.endpoint is None
    assert config.model is None
    assert config.api_key_env is None
    assert config.google_keyring_service == "mini-crm"
    assert config.google_keyring_user == "default"
    assert config.gmail_max_messages == 100
    assert config.gmail_newer_than_days == 30


def test_gmail_import_limits_are_validated(tmp_path):
    credentials = tmp_path / "client.json"
    credentials.write_text("{}", encoding="utf-8")
    values = {
        "MINI_CRM_MODE": "Dry-run",
        "MINI_CRM_PROVIDER": "gmail",
        "MINI_CRM_TEST_MAILBOX": "crm-test@example.invalid",
        "MINI_CRM_GOOGLE_CREDENTIALS_FILE": str(credentials),
        "MINI_CRM_GMAIL_MAX_MESSAGES": "25",
        "MINI_CRM_GMAIL_NEWER_THAN_DAYS": "7",
    }
    config = load_config(values)
    assert config.gmail_max_messages == 25
    assert config.gmail_newer_than_days == 7

    with pytest.raises(ConfigurationError, match="MAX_MESSAGES"):
        load_config({**values, "MINI_CRM_GMAIL_MAX_MESSAGES": "0"})
    with pytest.raises(ConfigurationError, match="NEWER_THAN_DAYS"):
        load_config({**values, "MINI_CRM_GMAIL_NEWER_THAN_DAYS": "yesterday"})


def test_gmail_keyring_names_must_be_safe(tmp_path):
    credentials = tmp_path / "client.json"
    credentials.write_text("{}", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="KEYRING"):
        load_config({
            "MINI_CRM_MODE": "Live",
            "MINI_CRM_PROVIDER": "gmail",
            "MINI_CRM_TEST_MAILBOX": "crm-test@example.invalid",
            "MINI_CRM_GOOGLE_CREDENTIALS_FILE": str(credentials),
            "MINI_CRM_GOOGLE_KEYRING_USER": "user/with/slash",
        })


def test_gmail_requires_credentials_file():
    with pytest.raises(ConfigurationError, match="MINI_CRM_GOOGLE_CREDENTIALS_FILE"):
        load_config({
            "MINI_CRM_MODE": "Dry-run",
            "MINI_CRM_PROVIDER": "gmail",
            "MINI_CRM_TEST_MAILBOX": "crm-test@example.invalid",
        })


def test_gmail_credentials_must_be_external_regular_readable_file(tmp_path):
    inside_repo = __import__("pathlib").Path(__file__).resolve().parents[1] / "credentials.json"
    with pytest.raises(ConfigurationError, match="outside"):
        load_config({
            "MINI_CRM_MODE": "Dry-run",
            "MINI_CRM_PROVIDER": "gmail",
            "MINI_CRM_TEST_MAILBOX": "crm-test@example.invalid",
            "MINI_CRM_GOOGLE_CREDENTIALS_FILE": str(inside_repo),
        })
    with pytest.raises(ConfigurationError, match="readable file"):
        load_config({
            "MINI_CRM_MODE": "Dry-run",
            "MINI_CRM_PROVIDER": "gmail",
            "MINI_CRM_TEST_MAILBOX": "crm-test@example.invalid",
            "MINI_CRM_GOOGLE_CREDENTIALS_FILE": str(tmp_path / "missing.json"),
        })
