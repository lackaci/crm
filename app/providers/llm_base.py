"""Interface for language model providers."""

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """Common interface for cloud and local language models."""

    @abstractmethod
    def complete(self, system: str, prompt: str, json_mode: bool = False) -> str:
        """Return a completion for a prompt."""

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Return a numeric representation of text."""
