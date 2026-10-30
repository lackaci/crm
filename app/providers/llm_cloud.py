"""Dependency-free, opt-in HTTP LLM provider."""

import json
import os
import re
import socket
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlparse

from .llm_base import CustomerDataNotAllowedError, LLMProvider
from app.privacy import mask_text


class CloudLLMError(RuntimeError):
    """A bounded, safe-to-display cloud provider failure."""


class CloudLLMProvider(LLMProvider):
    """OpenAI-compatible adapter; no vendor SDK is used."""

    def __init__(self, endpoint: str, model: str, api_key_env: str = "LLM_API_KEY",
                 *, timeout: float = 10.0, retries: int = 2, opener: Callable = urlopen,
                 audit_callback: Callable[[dict], None] | None = None,
                 prompt_version: str = "reply_v1"):
        if not endpoint.strip() or not model.strip():
            raise ValueError("endpoint and model are required")
        try:
            parsed = urlparse(endpoint)
            hostname = parsed.hostname
            port = parsed.port
        except ValueError as error:
            raise ValueError("endpoint must be a safe absolute HTTPS URL") from error
        if (
            parsed.scheme.lower() != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or not hostname
            or port == 0
            or any(character.isspace() for character in endpoint)
        ):
            raise ValueError("endpoint must be a safe absolute HTTPS URL")
        if not 0 < timeout <= 60 or not 0 <= retries <= 5:
            raise ValueError("timeout or retries outside safe bounds")
        self.endpoint, self.model, self.api_key_env = endpoint, model, api_key_env
        self.timeout, self.retries, self.opener = timeout, retries, opener
        self.audit_callback, self.prompt_version = audit_callback, prompt_version

    def complete(self, system: str, prompt: str, json_mode: bool = False) -> str:
        """Fail closed for every direct caller, before credentials or transport."""
        raise CustomerDataNotAllowedError(
            "cloud completion requires the approved masking boundary"
        )

    def complete_masked(
        self,
        system: str,
        prompt: str,
        json_mode: bool = False,
    ) -> str:
        """Complete already-masked text; restoration belongs to the wrapper."""
        if not isinstance(system, str) or not isinstance(prompt, str):
            raise CustomerDataNotAllowedError("cloud provider requires masked text")
        if mask_text(system).replacements or mask_text(prompt).replacements:
            raise CustomerDataNotAllowedError(
                "cloud provider requires already-masked text"
            )
        return self._complete_masked(system, prompt, json_mode)

    def _complete_masked(self, system: str, prompt: str, json_mode: bool = False) -> str:
        """Complete a request and emit one redacted audit record.

        Missing credentials and the customer-data guard are deliberately not
        audited: neither case invokes the provider.  Once the API request is
        eligible to run, the ``try`` block below owns exactly one audit
        emission, including failures from transport or response validation.
        """
        key = os.getenv(self.api_key_env)
        if not key:
            raise CloudLLMError(f"Missing API key environment variable: {self.api_key_env}")
        retry_count = 0
        usage = {}
        try:
            payload = {"model": self.model, "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ]}
            if json_mode:
                payload["response_format"] = {"type": "json_object"}
            request = Request(self.endpoint, data=json.dumps(payload).encode("utf-8"),
                              headers={"Authorization": "Bearer " + key,
                                       "Content-Type": "application/json"}, method="POST")
            data, last_error = None, None
            for attempt in range(self.retries + 1):
                try:
                    with self.opener(request, timeout=self.timeout) as response:
                        data = json.loads(response.read().decode("utf-8"))
                    break
                except (HTTPError, URLError, TimeoutError, socket.timeout, OSError,
                        json.JSONDecodeError) as error:
                    last_error = error
                    retry_count = attempt
                    if isinstance(error, json.JSONDecodeError):
                        break
                    if (isinstance(error, HTTPError)
                            and error.code != 429
                            and not 500 <= error.code <= 599):
                        break
            if data is None:
                raise CloudLLMError(
                    "cloud provider request failed after bounded retries"
                ) from last_error
            try:
                content = data["choices"][0]["message"]["content"]
                if not isinstance(content, str) or not content.strip():
                    raise ValueError
                usage = data.get("usage") if isinstance(data, dict) else {}
                if json_mode and not isinstance(json.loads(content), dict):
                    raise ValueError
            except (KeyError, IndexError, TypeError, ValueError,
                    json.JSONDecodeError) as error:
                raise CloudLLMError("cloud provider returned malformed response") from error
        except CloudLLMError as error:
            malformed = "malformed" in str(error)
            response_decode = isinstance(locals().get("last_error"), json.JSONDecodeError)
            transport_error = locals().get("last_error")
            self._audit(prompt, usage, retry_count, "failure",
                        "response" if malformed or response_decode else "transport",
                        "malformed_response" if malformed or response_decode
                        else (type(transport_error).__name__
                              if transport_error is not None else "request_failed"))
            raise
        except Exception:
            # Do not expose or persist unexpected exception details.
            self._audit(prompt, usage, retry_count, "failure", "internal",
                        "provider_error")
            raise
        self._audit(prompt, usage, retry_count, "success", None, None)
        return content

    def _audit(self, prompt: str, usage: object, retry_count: int,
               outcome: str, error_category: str | None,
               error_type: str | None) -> None:
        if not self.audit_callback:
            return
        safe_usage = usage if isinstance(usage, dict) else {}
        self.audit_callback({
            "provider": "cloud",
            "model": self.model,
            "prompt_version": self.prompt_version,
            "faq_id": _faq_id(prompt),
            "outcome": outcome,
            "error_category": error_category,
            "error_type": error_type,
            "retry_count": retry_count,
            "prompt_tokens": _integer(safe_usage.get("prompt_tokens")),
            "completion_tokens": _integer(safe_usage.get("completion_tokens")),
            "total_tokens": _integer(safe_usage.get("total_tokens")),
            "cost": safe_usage.get("cost"),
        })

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError("Configure an embedding endpoint before using cloud matching.")


def _faq_id(prompt: str) -> int | None:
    match = re.search(r"^FAQ_ID:\s*(\d+)", prompt, re.MULTILINE)
    return int(match.group(1)) if match else None


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
