"""Explicit online AI configuration, credential storage and bounded API calls."""
from dataclasses import dataclass
from hashlib import sha256
import json
import os
import re
import time
from urllib.parse import urlparse

import httpx

from .document_model import DocumentError

DEFAULT_ONLINE_MODEL = "gpt-5.4-mini-2026-03-17"
OPENAI_ENDPOINT = "https://api.openai.com/v1/responses"
VERCEL_ENDPOINT = "https://ai-gateway.vercel.sh/v1/chat/completions"
VERCEL_MODEL = "openai/gpt-5.4-mini"
VERCEL_EMBEDDING_MODEL = "openai/text-embedding-3-small"
VERCEL_KEY_ENV = "AI_GATEWAY_API_KEY"
KEYRING_SERVICE = "mini-crm-online-ai"


class OnlineSetupError(DocumentError):
    fatal = True


@dataclass(frozen=True)
class OnlineSettings:
    model: str = DEFAULT_ONLINE_MODEL
    provider: str = "openai"
    endpoint: str = OPENAI_ENDPOINT
    api_key_env: str = "OPENAI_API_KEY"
    embedding_model: str = "text-embedding-3-small"

    def __post_init__(self):
        if self.provider not in {"openai", "compatible", "vercel"}:
            raise DocumentError("Choose an online provider.")
        for model in (self.model, self.embedding_model):
            if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", model) or "://" in model:
                raise DocumentError("Enter a valid online model ID.")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,99}", self.api_key_env):
            raise DocumentError("Enter the API key environment variable name, not the key itself.")
        try:
            target = urlparse(self.endpoint)
            port = target.port
        except ValueError:
            raise DocumentError("Enter a valid HTTPS API endpoint.") from None
        if (target.scheme != "https" or not target.hostname or target.username or target.password or target.query or target.fragment
                or port == 0 or any(character.isspace() for character in self.endpoint)):
            raise DocumentError("Online AI requires an HTTPS endpoint without embedded credentials or query parameters.")
        if self.provider == "openai" and self.endpoint != OPENAI_ENDPOINT:
            raise DocumentError("OpenAI uses its fixed official API endpoint. Choose a compatible provider for another service.")
        if self.provider == "vercel":
            if self.endpoint != VERCEL_ENDPOINT:
                raise DocumentError("Vercel AI Gateway uses its fixed official API endpoint.")
            if any(not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9][A-Za-z0-9._:/-]*", model)
                   for model in (self.model, self.embedding_model)):
                raise DocumentError("Vercel model IDs need a creator prefix, such as openai/gpt-5.4-mini.")
        if self.provider == "compatible" and not target.path.endswith("/chat/completions"):
            raise DocumentError("A compatible provider needs its full HTTPS /chat/completions endpoint.")
        if self.provider == "compatible" and target.hostname in {"api.example.com", "example.com"}:
            raise DocumentError("Replace the example endpoint with your provider's real API endpoint.")

    @property
    def keyring_user(self):
        return sha256((self.provider + "|" + self.endpoint + "|" + self.api_key_env).encode()).hexdigest()

    @classmethod
    def vercel(cls):
        return cls(VERCEL_MODEL, "vercel", VERCEL_ENDPOINT, VERCEL_KEY_ENV, VERCEL_EMBEDDING_MODEL)

    @classmethod
    def from_config(cls, config):
        return cls(model=config["online_model"], provider=config["provider"], endpoint=config["online_endpoint"],
            api_key_env=config["api_key_env"], embedding_model=config["embedding_model"])


def save_api_key(settings, key):
    if not isinstance(key, str) or not key.strip() or len(key) > 4096 or any(character in key for character in "\r\n\x00"):
        raise DocumentError("Enter a valid API key in the masked key field.")
    try:
        import keyring
        keyring.set_password(KEYRING_SERVICE, settings.keyring_user, key.strip())
    except Exception:
        raise DocumentError("The API key could not be saved in the OS keyring. You can use its environment variable instead.") from None


def api_key(settings):
    try:
        import keyring
        key = keyring.get_password(KEYRING_SERVICE, settings.keyring_user)
    except Exception:
        key = None
    key = key or os.getenv(settings.api_key_env)
    if not key:
        raise OnlineSetupError("Add an online API key in Files > Excel register, or set " + settings.api_key_env + ".")
    if not isinstance(key, str) or len(key) > 4096 or any(character in key for character in "\r\n\x00"):
        raise OnlineSetupError("The online API key is invalid. Replace it in the masked key field.")
    return key.strip()


class OnlineClient:
    def __init__(self, settings=None):
        self.settings = settings or OnlineSettings()
        self.model = self.settings.model

    def require_key(self):
        api_key(self.settings)

    def request(self, payload, *, embedding=False):
        endpoint = self.settings.endpoint
        if embedding:
            suffix = "/responses" if self.settings.provider == "openai" else "/chat/completions"
            endpoint = endpoint[:-len(suffix)] + "/embeddings"
        key = api_key(self.settings)
        try:
            deadline = time.monotonic() + 130
            with httpx.Client(timeout=httpx.Timeout(120, connect=10), trust_env=False, follow_redirects=False,
                              transport=httpx.HTTPTransport(retries=0)) as client:
                with client.stream("POST", endpoint, json=payload, headers={"Authorization": "Bearer " + key}) as response:
                    if response.status_code in {401, 403}:
                        raise OnlineSetupError("Online authentication failed. Check the API key and model access.")
                    if response.status_code == 429:
                        raise OnlineSetupError("Online rate limit or quota reached. Check your provider account before retrying.")
                    if response.status_code >= 300:
                        raise OnlineSetupError("The online provider rejected the request. Check the endpoint, model and structured-output support.")
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 2_000_000 or time.monotonic() > deadline:
                            raise DocumentError("The online response exceeded its size or time limit.")
                    value = json.loads(raw)
                    if not isinstance(value, dict):
                        raise DocumentError("The online provider returned an invalid response.")
                    return value
        except DocumentError:
            raise
        except httpx.HTTPError:
            raise OnlineSetupError("The online AI request failed. Check the connection before retrying; no automatic retry was made.") from None
        except (ValueError, UnicodeError):
            raise DocumentError("The online provider returned invalid JSON.") from None

    def complete_json(self, instructions, content, schema, *, image=None):
        import base64
        if self.settings.provider == "openai":
            parts = [{"type": "input_text", "text": content}]
            if image:
                parts.append({"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii"), "detail": "high"})
            response = self.request({"model": self.model, "store": False, "stream": False, "max_output_tokens": 8192,
                "instructions": instructions, "input": [{"role": "user", "content": parts}],
                "text": {"format": {"type": "json_schema", "name": "crm_extraction", "strict": True, "schema": schema}}})
            if response.get("status") != "completed":
                raise DocumentError("The online model did not complete its response. No partial result was saved.")
            texts = []
            output = response.get("output")
            if not isinstance(output, list):
                raise DocumentError("The online provider returned invalid output data.")
            for entry in output:
                if not isinstance(entry, dict):
                    raise DocumentError("The online provider returned an invalid output entry.")
                if entry.get("type") != "message":
                    continue
                for part in entry.get("content", []):
                    if not isinstance(part, dict):
                        raise DocumentError("The online provider returned invalid output content.")
                    if part.get("type") == "refusal":
                        raise DocumentError("The online model declined this extraction.")
                    if part.get("type") == "output_text":
                        texts.append(part.get("text", ""))
            text = "".join(texts)
        else:
            parts = [{"type": "text", "text": content}]
            if image:
                parts.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii"), "detail": "high"}})
            response = self.request({"model": self.model, "stream": False, "max_completion_tokens": 8192,
                "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": parts}],
                "response_format": {"type": "json_schema", "json_schema": {"name": "crm_extraction", "strict": True, "schema": schema}}})
            try:
                choice = response["choices"][0]
                if choice.get("finish_reason") != "stop" or choice["message"].get("refusal"):
                    raise DocumentError("The online model did not return a complete permitted result.")
                text = choice["message"]["content"]
            except (KeyError, IndexError, TypeError):
                raise DocumentError("The compatible provider returned an invalid completion.") from None
        try:
            value = json.loads(text)
        except (ValueError, TypeError):
            raise DocumentError("The online model did not return valid extraction JSON.") from None
        if not isinstance(value, dict):
            raise DocumentError("The online model did not return an extraction object.")
        return value
