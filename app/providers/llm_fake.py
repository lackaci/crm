"""Deterministic offline language model used by tests and development."""

from .llm_base import LLMProvider


class FakeLLMProvider(LLMProvider):
    """A safe placeholder that never calls a network service."""

    def complete(self, system: str, prompt: str, json_mode: bool = False) -> str:
        """Return a deterministic response without model knowledge."""
        if json_mode:
            return '{"status": "fake", "text": "Offline test response"}'
        return "Offline test response"

    def embed(self, text: str) -> list[float]:
        """Return a stable, simple embedding for offline tests."""
        return [float(len(text))]
