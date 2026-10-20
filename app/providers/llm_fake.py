"""Deterministic offline language model used by tests and development."""

import hashlib
import json
import re

from .llm_base import LLMProvider


class FakeLLMProvider(LLMProvider):
    """A safe placeholder that never calls a network service."""

    def complete(self, system: str, prompt: str, json_mode: bool = False) -> str:
        """Return a deterministic response without model knowledge."""
        if json_mode:
            # This is deliberately a mechanical rewrite, not an answer from knowledge.
            faq_id = re.search(r"FAQ_ID:\s*(\d+)", prompt)
            answer = re.search(r"APPROVED_ANSWER:\s*(.*?)(?:\nCUSTOMER_NAME:|\Z)", prompt, re.S)
            name = re.search(r"CUSTOMER_NAME:\s*(.*)", prompt)
            if faq_id and answer:
                greeting = f"Hello {name.group(1).strip()}," if name else "Hello,"
                return json.dumps({
                    "faq_id": int(faq_id.group(1)),
                    "reply": f"{greeting}\n{answer.group(1).strip()}",
                })
            return '{"status": "fake", "text": "Offline test response"}'
        return "Offline test response"

    def embed(self, text: str) -> list[float]:
        """Return a stable bag-of-words vector, requiring shared words to match."""
        vector = [0.0] * 64
        stop_words = {"a", "an", "and", "are", "can", "do", "how", "i", "is",
                      "my", "on", "the", "to", "when", "what", "you", "your"}
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            if word in stop_words:
                continue
            index = int(hashlib.sha256(word.encode()).hexdigest(), 16) % len(vector)
            vector[index] += 1.0
        return vector
