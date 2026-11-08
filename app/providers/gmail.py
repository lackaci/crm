"""Gmail imports and explicitly reviewed draft/send operations.

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
from urllib.parse import urlparse

from app.config import ConfigurationError
from .email_base import EmailProvider, DeliveryRejected, DeliveryUncertain, ReviewedReply
from email.message import EmailMessage
from email.policy import SMTP

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_COMPOSE_SCOPE = "https://www.googleapis.com/auth/gmail.compose"
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
    """Import messages; expose reviewed writes only with compose consent."""

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
        write_enabled: bool = False,
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
        self.write_enabled = write_enabled

    def _service_or_login(self, allow_login: bool = True) -> Any:
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
        scopes = [GMAIL_READONLY_SCOPE] + ([GMAIL_COMPOSE_SCOPE] if self.write_enabled else [])
        if token:
            try:
                info = json.loads(token)
                stored_scopes = info.get("scopes", [])
                if self.write_enabled and not set(scopes).issubset(set(stored_scopes)):
                    credentials = None
                else:
                    credentials = Credentials.from_authorized_user_info(info, scopes)
            except (ValueError, TypeError, json.JSONDecodeError):
                credentials = None
        if credentials is not None and not credentials.valid and getattr(credentials, "refresh_token", None):
            try:
                from google.auth.transport.requests import Request
                credentials.refresh(Request())
                backend.set_password(self.keyring_service, self.keyring_user, credentials.to_json())
            except Exception:
                credentials = None
        if credentials is None or not credentials.valid:
            if not allow_login:
                raise ConfigurationError("Reconnect Gmail in Settings to approve the required permissions.")
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
                flow = flow_factory(str(self.credentials_file), scopes)
                options = {"port": 0}
                if self.write_enabled:
                    options.update(prompt="consent", access_type="offline")
                if self._flow_factory is None:
                    options["timeout_seconds"] = 180
                credentials = flow.run_local_server(**options)
                if self.write_enabled and not credentials.has_scopes(scopes):
                    raise ConfigurationError("Google did not grant the required Gmail permissions.")
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
            def builder(name, version, credentials):
                import httplib2
                import google_auth_httplib2
                authorized = google_auth_httplib2.AuthorizedHttp(credentials, http=httplib2.Http(timeout=30))
                return build(name, version, http=authorized, cache_discovery=False)
        else:
            builder = self._service_builder
        return builder("gmail", "v1", credentials=credentials)

    def verify_connection(self, allow_login: bool = True):
        if not self.expected_mailbox:
            raise DeliveryRejected("Configure the Gmail account before connecting.")
        try:
            service = self._service_or_login(allow_login=allow_login)
            profile = service.users().getProfile(userId="me").execute(num_retries=0)
            if str(profile.get("emailAddress", "")).strip().casefold() != self.expected_mailbox.casefold():
                raise DeliveryRejected("The signed-in Gmail account does not match the configured account.")
            return self.expected_mailbox
        except (ConfigurationError, DeliveryRejected):
            raise
        except Exception:
            raise DeliveryRejected("Gmail connection could not be verified. Reconnect in Settings.") from None

    def _write_service(self, reply: ReviewedReply):
        if not self.write_enabled:
            raise DeliveryRejected("Gmail draft and send permissions are disabled.")
        if not self.expected_mailbox or reply.sender.casefold() != self.expected_mailbox.casefold():
            raise DeliveryRejected("The sending account does not match this approved reply.")
        self.verify_connection(allow_login=False)
        return self._service_or_login(allow_login=False)

    def _raw_reply(self, service, reply):
        from email.utils import parseaddr
        for address in (reply.sender, reply.recipient):
            if not re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}", address) or parseaddr(address)[1] != address:
                raise DeliveryRejected("A single valid sender and recipient are required.")
        if any(char in reply.subject for char in "\r\n") or len(reply.subject) > 998:
            raise DeliveryRejected("Invalid reply subject.")
        message = EmailMessage(policy=SMTP)
        message["From"], message["To"], message["Subject"] = reply.sender, reply.recipient, reply.subject
        message["Message-ID"] = reply.rfc_message_id
        if reply.original_message_id:
            try:
                original = service.users().messages().get(userId="me", id=reply.original_message_id,
                    format="metadata", metadataHeaders=["Message-ID"]).execute(num_retries=0)
                reference = _header(original.get("payload", {}).get("headers", []), "Message-ID")
                if reference and len(reference) < 998 and "\r" not in reference and "\n" not in reference:
                    message["In-Reply-To"] = reference
                    message["References"] = reference
            except Exception:
                raise DeliveryRejected("The original Gmail message could not be verified.") from None
        message.set_content(reply.body)
        result = {"raw": base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")}
        if reply.thread_id:
            result["threadId"] = reply.thread_id
        return result

    def _mutation(self, request, delete=False):
        try:
            if self._service is not None:
                # Explicit service injection is for offline provider tests.
                return request.execute(num_retries=0)
            # httplib2 can replay a POST after BadStatusLine independently of
            # googleapiclient's num_retries. Mutations therefore use a fresh
            # HTTPX transport with zero retries and no redirects/proxies.
            import httpx
            target = urlparse(request.uri)
            if target.scheme != "https" or target.hostname not in {"gmail.googleapis.com", "www.googleapis.com"} or not target.path.startswith("/gmail/v1/users/me/drafts"):
                raise DeliveryRejected("Unexpected Gmail mutation endpoint.")
            credentials = getattr(request.http, "credentials", None)
            if credentials is None or not credentials.valid or not credentials.token:
                raise DeliveryRejected("Reconnect Gmail before creating or sending a draft.")
            headers = {**request.headers, "Authorization": "Bearer " + credentials.token}
            with httpx.Client(timeout=httpx.Timeout(30, connect=10), trust_env=False, follow_redirects=False,
                              transport=httpx.HTTPTransport(retries=0)) as client:
                with client.stream(request.method, request.uri, content=request.body, headers=headers) as response:
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 1_000_000:
                            raise DeliveryUncertain("Gmail's response was too large to verify. Check delivery status.")
                    if delete and response.status_code == 404:
                        return {}
                    if response.status_code in {400,401,403,404,413}:
                        raise DeliveryRejected("Gmail rejected the operation. Check the account permissions and reconnect.")
                    if response.status_code >= 300:
                        raise DeliveryUncertain("Gmail's response is uncertain. Check delivery status.")
                    if not raw and delete:
                        return {}
                    result = json.loads(raw)
                    if not isinstance(result, dict):
                        raise DeliveryUncertain("Gmail did not return a verifiable result. Check delivery status.")
                    return result
        except (DeliveryRejected, DeliveryUncertain):
            raise
        except Exception as error:
            status = getattr(getattr(error, "resp", None), "status", None)
            if delete and status == 404:
                return {}
            if status in {400, 401, 403, 404, 413}:
                raise DeliveryRejected("Gmail rejected the operation. Check the account permissions and reconnect.") from None
            raise DeliveryUncertain("Gmail's response is uncertain. Check delivery status before trying again.") from None

    def create_reviewed_draft(self, reply: ReviewedReply):
        service = self._write_service(reply)
        raw = self._raw_reply(service, reply)
        result = self._mutation(service.users().drafts().create(userId="me", body={"message": raw}))
        if not isinstance(result.get("id"), str) or not result["id"]:
            raise DeliveryUncertain("Gmail draft creation could not be confirmed. Check its status.")
        return result["id"]

    def send_reviewed_draft(self, reply: ReviewedReply, gmail_draft_id: str):
        service = self._write_service(reply)
        raw = self._raw_reply(service, reply)
        result = self._mutation(service.users().drafts().send(userId="me", body={"id": gmail_draft_id, "message": raw}))
        if not isinstance(result.get("id"), str) or not result["id"]:
            raise DeliveryUncertain("Gmail did not confirm a message ID. Check its status before retrying.")
        return result["id"]

    def lookup_reviewed_reply(self, reply: ReviewedReply, operation: str):
        service = self._write_service(reply)
        folder = "sent" if operation == "send" else "drafts"
        try:
            matches = service.users().messages().list(userId="me", q=f"in:{folder} rfc822msgid:{reply.rfc_message_id}", maxResults=2).execute(num_retries=0).get("messages", [])
            if len(matches) != 1:
                return None
            if operation == "send":
                return matches[0]["id"]
            token = None
            for _ in range(100):
                options = {"userId": "me", "maxResults": 100}
                if token:
                    options["pageToken"] = token
                page = service.users().drafts().list(**options).execute(num_retries=0)
                for draft in page.get("drafts", []):
                    if draft.get("message", {}).get("id") == matches[0]["id"]:
                        return draft["id"]
                token = page.get("nextPageToken")
                if not token:
                    break
            return None
        except Exception:
            raise DeliveryRejected("Gmail status could not be checked. No message was resent.") from None

    def delete_reviewed_draft(self, reply: ReviewedReply, gmail_draft_id: str):
        service = self._write_service(reply)
        try:
            self._mutation(service.users().drafts().delete(userId="me", id=gmail_draft_id), delete=True)
        except Exception as error:
            if getattr(getattr(error, "resp", None), "status", None) == 404:
                return
            raise DeliveryUncertain("Draft cancellation is not confirmed. Check it again before editing.") from None

    def fetch_new_emails(self, *, allow_login: bool = True) -> list[dict[str, Any]]:
        try:
            service = self._service_or_login(allow_login=allow_login)
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

    def import_incrementally(self, database_path, *, storage_root=None, progress=None):
        from app.gmail_import import synchronize_gmail
        return synchronize_gmail(database_path, self, storage_root=storage_root, progress=progress, allow_login=False)

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
        body = plain_body or _html_to_text(html_body) or "(No readable message text; check any attachments.)"
        headers = payload.get("headers", [])
        sender = _header(headers, "From")
        name, email = sender, sender
        if "<" in sender and sender.endswith(">"):
            name, email = sender.split("<", 1)
            email = email[:-1].strip()
        received_at = _header(headers, "Date") or None
        # Gmail's internal arrival date is trustworthy for ordering, unlike
        # sender-supplied Date headers (which may be invalid or backdated).
        if message.get("internalDate"):
            from datetime import datetime, timezone
            received_at = datetime.fromtimestamp(int(message["internalDate"]) / 1000, timezone.utc).isoformat()
        return {
            "provider": "gmail",
            "source_mailbox": self.expected_mailbox,
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
