"""Optional, dependency-free HTTP LLM provider.

The rest of the application does not need this module: FakeLLMProvider works
offline.  The key is read at call time from an environment variable.
"""

import json
import os
from urllib.request import Request, urlopen

from .llm_base import LLMProvider


class CloudLLMProvider(LLMProvider):
    """Small OpenAI-compatible provider without importing a vendor SDK."""

    def __init__(self, endpoint: str, model: str, api_key_env: str = "LLM_API_KEY"):
        self.endpoint = endpoint
        self.model = model
        self.api_key_env = api_key_env

    def complete(self, system: str, prompt: str, json_mode: bool = False) -> str:
        key = os.getenv(self.api_key_env)
        if not key:
            raise RuntimeError(f"Missing API key environment variable: {self.api_key_env}")
        payload = {"model": self.model, "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        request = Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
        return str(data["choices"][0]["message"]["content"])

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError("Configure an embedding endpoint before using cloud matching.")
