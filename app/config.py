"""Small, side-effect-free configuration loader for integration settings.

M08 deliberately only describes integrations.  It does not construct a
network client or read a secret value.  Later milestones can use the names
returned here when they add provider implementations.
"""

from dataclasses import dataclass
from enum import Enum
from os import environ
import os
from pathlib import Path
import re
from typing import Mapping
from urllib.parse import urlparse

from dotenv import load_dotenv

_PROJECT_ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
load_dotenv(_PROJECT_ROOT / ".env", override=False)


class RunMode(str, Enum):
    """The only run modes supported by the application."""

    MOCK = "Mock"
    DRY_RUN = "Dry-run"
    LIVE = "Live"


ALLOWED_PROVIDERS = frozenset({"mock", "fake", "gmail", "cloud"})


class ConfigurationError(ValueError):
    """Raised when an application configuration is unsafe or incomplete."""


_SAFE_KEYRING_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _validate_google_credentials_file(value: str) -> str:
    """Accept only an existing readable file outside this checkout."""
    path = Path(value).expanduser().resolve()
    try:
        path.relative_to(_PROJECT_ROOT)
    except ValueError:
        pass
    else:
        raise ConfigurationError(
            "MINI_CRM_GOOGLE_CREDENTIALS_FILE must be outside the repository."
        )
    if not path.is_file() or not os.access(path, os.R_OK):
        raise ConfigurationError(
            "MINI_CRM_GOOGLE_CREDENTIALS_FILE must be a readable file."
        )
    return str(path)


@dataclass(frozen=True)
class AppConfig:
    """Validated settings, containing names but never secret values."""

    mode: RunMode
    provider: str
    test_mailbox: str | None = None
    endpoint: str | None = None
    model: str | None = None
    api_key_env: str | None = None
    google_credentials_file: str | None = None
    google_keyring_service: str = "mini-crm"
    google_keyring_user: str = "default"
    gmail_allowed_senders: tuple[str, ...] = ()
    gmail_max_messages: int = 100
    gmail_newer_than_days: int | None = 30

    @property
    def sending_enabled(self) -> bool:
        """Sending remains disabled for every mode during M08."""
        return False


def load_config(values: Mapping[str, str] | None = None) -> AppConfig:
    """Load and validate configuration from environment-style string values.

    ``values`` is useful for deterministic tests.  With no argument the
    process environment is read directly; no dotenv or secret-store package
    is involved.
    """

    source = environ if values is None else values
    mode_text = source.get("MINI_CRM_MODE", RunMode.MOCK.value)
    try:
        mode = RunMode(mode_text)
    except ValueError as error:
        raise ConfigurationError(
            "MINI_CRM_MODE must be exactly Mock, Dry-run, or Live."
        ) from error

    provider = source.get("MINI_CRM_PROVIDER", "mock").strip().lower()
    if provider not in ALLOWED_PROVIDERS:
        allowed = ", ".join(sorted(ALLOWED_PROVIDERS))
        raise ConfigurationError(f"Unknown MINI_CRM_PROVIDER; choose: {allowed}.")

    def required(name: str) -> str:
        value = source.get(name, "").strip()
        if not value:
            raise ConfigurationError(
                f"{name} is required when MINI_CRM_MODE is {mode.value}."
            )
        return value

    if mode is RunMode.MOCK:
        credentials = source.get("MINI_CRM_GOOGLE_CREDENTIALS_FILE", "").strip()
        return AppConfig(
            mode=mode,
            provider=provider,
            google_credentials_file=(
                _validate_google_credentials_file(credentials) if credentials else None
            ),
            google_keyring_service=source.get("MINI_CRM_GOOGLE_KEYRING_SERVICE", "mini-crm"),
            google_keyring_user=source.get("MINI_CRM_GOOGLE_KEYRING_USER", "default"),
        )

    # The mailbox is shared by the external providers.  It must be a separate
    # test mailbox; the provider-specific settings below are intentionally not
    # requested for providers that do not use them.
    mailbox = required("MINI_CRM_TEST_MAILBOX")
    endpoint = model = api_key_env = None
    credentials_file = None
    keyring_service = source.get("MINI_CRM_GOOGLE_KEYRING_SERVICE", "mini-crm").strip()
    keyring_user = source.get("MINI_CRM_GOOGLE_KEYRING_USER", "default").strip()
    allowed_senders = tuple(
        sender.strip().lower()
        for sender in source.get("MINI_CRM_GMAIL_ALLOWED_SENDERS", "").split(",")
        if sender.strip()
    )
    max_messages_text = source.get("MINI_CRM_GMAIL_MAX_MESSAGES", "100").strip()
    try:
        max_messages = int(max_messages_text)
    except ValueError as error:
        raise ConfigurationError(
            "MINI_CRM_GMAIL_MAX_MESSAGES must be a positive whole number."
        ) from error
    if max_messages <= 0:
        raise ConfigurationError(
            "MINI_CRM_GMAIL_MAX_MESSAGES must be a positive whole number."
        )
    newer_than_text = source.get("MINI_CRM_GMAIL_NEWER_THAN_DAYS", "30").strip()
    if newer_than_text.lower() in {"", "none", "all"}:
        newer_than_days = None
    else:
        try:
            newer_than_days = int(newer_than_text)
        except ValueError as error:
            raise ConfigurationError(
                "MINI_CRM_GMAIL_NEWER_THAN_DAYS must be a positive whole number or 'none'."
            ) from error
        if newer_than_days <= 0:
            raise ConfigurationError(
                "MINI_CRM_GMAIL_NEWER_THAN_DAYS must be a positive whole number or 'none'."
            )

    if provider == "cloud":
        # These are setting names/connection metadata only. Values are retained
        # in memory for validation and never read as secret values.
        endpoint = required("MINI_CRM_ENDPOINT")
        parsed_endpoint = urlparse(endpoint)
        try:
            port = parsed_endpoint.port
        except ValueError as error:
            raise ConfigurationError(
                "MINI_CRM_ENDPOINT must have a valid HTTPS port."
            ) from error
        if (
            parsed_endpoint.scheme.lower() != "https"
            or not parsed_endpoint.hostname
            or parsed_endpoint.username is not None
            or parsed_endpoint.password is not None
            or any(character.isspace() for character in endpoint)
            or port == 0
        ):
            raise ConfigurationError(
                "MINI_CRM_ENDPOINT must be a safe absolute HTTPS URL."
            )
        model = required("MINI_CRM_MODEL")
        api_key_env = required("MINI_CRM_API_KEY_ENV")
    elif provider == "gmail":
        credentials_file = _validate_google_credentials_file(
            required("MINI_CRM_GOOGLE_CREDENTIALS_FILE")
        )
        for name, value in (
            ("MINI_CRM_GOOGLE_KEYRING_SERVICE", keyring_service),
            ("MINI_CRM_GOOGLE_KEYRING_USER", keyring_user),
        ):
            if not _SAFE_KEYRING_NAME.fullmatch(value):
                raise ConfigurationError(
                    f"{name} must contain only letters, numbers, '.', '_' or '-'."
                )

    return AppConfig(
        mode=mode,
        provider=provider,
        test_mailbox=mailbox,
        endpoint=endpoint,
        model=model,
        api_key_env=api_key_env,
        google_credentials_file=credentials_file,
        google_keyring_service=keyring_service,
        google_keyring_user=keyring_user,
        gmail_allowed_senders=allowed_senders,
        gmail_max_messages=max_messages,
        gmail_newer_than_days=newer_than_days,
    )
