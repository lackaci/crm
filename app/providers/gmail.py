"""Read-only Gmail adapter.

The Google libraries are imported only in this module and only when a real
provider is constructed.  Tests can inject a service and keyring backend.
"""

import base64
import binascii
import json
import mimetypes
from html.parser import HTMLParser
from pathlib import Path
import re
from typing import Any, Callable

from app.config import ConfigurationError
from .email_base import EmailProvider

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
KEYRING_SERVICE = "mini-crm"
KEYRING_USER = "default"


class GmailSyncError(RuntimeError):
    """A user-visible, non-silent Gmail synchronization failure."""


def _header(headers: list[dict[str, str]], name: str) -> str:
    for item in headers:
        if item.get("name", "").lower() == name.lower():
            return item.get("value", "")
    return ""


def _decode(value: str) -> str:
    return _decode_bytes(value).decode("utf-8", errors="replace")


def _decode_bytes(value: str) -> bytes:
    try:
        raw = base64.urlsafe_b64decode(value.encode("ascii") + b"===")
    except (ValueError, binascii.Error):
        raise GmailSyncError("Gmail returned an invalid message encoding.") from None
    return raw


class _EmailHTMLParser(HTMLParser):
    """Extract readable text from an HTML-only email body."""

    _BLOCK_TAGS = {"div", "p", "li", "tr", "h1", "h2", "h3", "h4", "br"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"style", "script"}:
            self._ignored_depth += 1
        elif self._ignored_depth == 0 and tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"style", "script"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif self._ignored_depth == 0 and tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth == 0:
            self.parts.append(data)


def _html_to_text(value: str) -> str:
    parser = _EmailHTMLParser()
    parser.feed(value)
    parser.close()
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class GmailProvider(EmailProvider):
    """Fetch Gmail messages without exposing any write operation."""

    provider_name = "gmail"

    def __init__(
        self,
        credentials_file: Path | str | None = None,
        *,
        service: Any | None = None,
        keyring_backend: Any | None = None,
        flow_factory: Callable[..., Any] | None = None,
        service_builder: Callable[..., Any] | None = None,
        keyring_service: str = KEYRING_SERVICE,
        keyring_user: str = KEYRING_USER,
        expected_mailbox: str | None = None,
        allowed_senders: tuple[str, ...] = (),
        max_messages: int = 100,
        newer_than_days: int | None = 30,
    ) -> None:
        self.credentials_file = Path(credentials_file) if credentials_file else None
        self._service = service
        self._keyring = keyring_backend
        self._flow_factory = flow_factory
        self._service_builder = service_builder
        self.keyring_service = keyring_service
        self.keyring_user = keyring_user
        self.expected_mailbox = expected_mailbox.strip() if expected_mailbox else None
        self.allowed_senders = tuple(
            sender.strip().casefold() for sender in allowed_senders if sender.strip()
        )
        if max_messages <= 0:
            raise ValueError("max_messages must be positive.")
        if newer_than_days is not None and newer_than_days <= 0:
            raise ValueError("newer_than_days must be positive or None.")
        self.max_messages = max_messages
        self.newer_than_days = newer_than_days

    def _service_or_login(self) -> Any:
        if self._service is not None:
            return self._service
        if self.credentials_file is None:
            raise ConfigurationError("Set MINI_CRM_GOOGLE_CREDENTIALS_FILE for Gmail login.")
        if not self.credentials_file.is_file():
            raise ConfigurationError("The configured Google credentials file was not found.")
        if self._keyring is None:
            try:
                import keyring
            except ImportError as error:
                raise ConfigurationError(
                    "Gmail support needs the keyring package."
                ) from None
            backend = keyring
        else:
            backend = self._keyring
        try:
            from google.oauth2.credentials import Credentials
        except ImportError:
            raise ConfigurationError(
                "Gmail support needs google-auth."
            ) from None
        try:
            token = backend.get_password(self.keyring_service, self.keyring_user)
        except Exception:
            raise ConfigurationError("Could not read the Gmail token from keyring.") from None
        credentials = None
        if token:
            try:
                credentials = Credentials.from_authorized_user_info(json.loads(token), [GMAIL_READONLY_SCOPE])
            except (ValueError, TypeError, json.JSONDecodeError):
                credentials = None
        if credentials is None or not credentials.valid:
            if self._flow_factory is None:
                try:
                    from google_auth_oauthlib.flow import InstalledAppFlow
                except ImportError:
                    raise ConfigurationError(
                        "Gmail support needs google-auth-oauthlib."
                    ) from None
                flow_factory = InstalledAppFlow.from_client_secrets_file
            else:
                flow_factory = self._flow_factory
            try:
                flow = flow_factory(str(self.credentials_file), [GMAIL_READONLY_SCOPE])
                credentials = flow.run_local_server(port=0)
                token_json = credentials.to_json()
                backend.set_password(
                    self.keyring_service,
                    self.keyring_user,
                    token_json,
                )
            except Exception:
                raise ConfigurationError(
                    "Gmail OAuth login failed; no token was stored in a file."
                ) from None
        if self._service_builder is None:
            try:
                from googleapiclient.discovery import build
            except ImportError:
                raise ConfigurationError(
                    "Gmail support needs google-api-python-client."
                ) from None
            builder = build
        else:
            builder = self._service_builder
        return builder("gmail", "v1", credentials=credentials)

    def fetch_new_emails(self) -> list[dict[str, Any]]:
        try:
            service = self._service_or_login()
            # A directly injected service is used by offline tests.  Those
            # tests may omit the mailbox, but a configured Gmail login may
            # never list messages until its authenticated identity is checked.
            if self.expected_mailbox is None:
                if self._service is None:
                    raise ConfigurationError(
                        "MINI_CRM_TEST_MAILBOX is required for Gmail synchronization."
                    )
            else:
                try:
                    profile = service.users().getProfile(userId="me").execute()
                except Exception:
                    raise GmailSyncError(
                        "Gmail authentication could not be verified; no messages were imported."
                    ) from None
                authenticated_mailbox = profile.get("emailAddress")
                if (
                    not isinstance(authenticated_mailbox, str)
                    or not authenticated_mailbox.strip()
                    or authenticated_mailbox.casefold() != self.expected_mailbox.casefold()
                ):
                    raise GmailSyncError(
                        "The configured Gmail mailbox could not be verified; no messages were imported."
                    )
            message_ids: dict[str, None] = {}
            queries = (
                [f"from:{sender}" for sender in self.allowed_senders]
                if self.allowed_senders
                else [None]
            )
            for query in queries:
                options: dict[str, Any] = {"userId": "me"}
                if query is not None:
                    options["q"] = query
                if self.newer_than_days is not None:
                    options["q"] = (
                        f"{options.get('q', '')} newer_than:{self.newer_than_days}d"
                    ).strip()
                while len(message_ids) < self.max_messages:
                    response = service.users().messages().list(**options).execute()
                    for item in response.get("messages", []):
                        if isinstance(item, dict) and item.get("id"):
                            message_ids[str(item["id"])] = None
                            if len(message_ids) >= self.max_messages:
                                break
                    next_page = response.get("nextPageToken")
                    if not next_page or len(message_ids) >= self.max_messages:
                        break
                    options["pageToken"] = next_page
            result = []
            for message_id in message_ids:
                message = service.users().messages().get(
                    userId="me", id=message_id, format="full"
                ).execute()
                normalized = self._normalize(message, service)
                if (
                    not self.allowed_senders
                    or normalized["from_email"].casefold() in self.allowed_senders
                ):
                    result.append(normalized)
            return result
        except (ConfigurationError, GmailSyncError):
            raise
        except Exception:
            # Do not chain provider exceptions: Google errors can contain
            # message payloads, account identifiers, or credential material.
            raise GmailSyncError(
                "Gmail synchronization failed; no messages were imported."
            ) from None

    def _normalize(self, message: dict[str, Any], service: Any) -> dict[str, Any]:
        payload = message.get("payload", {})
        plain_body = ""
        html_body = ""
        attachments = []

        def walk(part: dict[str, Any]) -> None:
            nonlocal plain_body, html_body
            data = part.get("body", {}).get("data")
            filename = part.get("filename", "")
            attachment_id = part.get("body", {}).get("attachmentId")
            if filename and attachment_id:
                content = service.users().messages().attachments().get(
                    userId="me", messageId=message["id"], id=attachment_id
                ).execute().get("data", "")
                attachments.append({
                    "attachment_id": attachment_id,
                    "filename": filename,
                    "mime_type": part.get("mimeType") or mimetypes.guess_type(filename)[0] or "application/octet-stream",
                    "content": _decode_bytes(content),
                })
            elif data and part.get("mimeType") == "text/plain" and not plain_body:
                plain_body = _decode(data)
            elif data and part.get("mimeType") == "text/html" and not html_body:
                html_body = _decode(data)
            for child in part.get("parts", []) or []:
                walk(child)
        walk(payload)
        body = plain_body or _html_to_text(html_body)
        headers = payload.get("headers", [])
        sender = _header(headers, "From")
        name, email = sender, sender
        if "<" in sender and sender.endswith(">"):
            name, email = sender.split("<", 1)
            email = email[:-1].strip()
        received_at = _header(headers, "Date") or None
        return {
            "provider": "gmail",
            "remote_message_id": message.get("id"),
            "remote_thread_id": message.get("threadId"),
            "message_id": message.get("id"),
            "thread_id": message.get("threadId"),
            "from_name": name.strip() or email,
            "from_email": email.strip(),
            "sender": email.strip(),
            "subject": _header(headers, "Subject") or "(no subject)",
            "body": body,
            "received_at": received_at,
            "date": received_at,
            "labels": message.get("labelIds", []),
            "attachments": attachments,
        }
