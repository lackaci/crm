"""Provider decorator that makes masking unavoidable for every LLM method."""

from app.privacy import mask_text, restore_text
from .llm_base import LLMProvider


class MaskedLLMProvider(LLMProvider):
    """Mask before delegation and restore only a safe, matching response."""

    accepts_customer_data = True

    def __init__(self, provider: LLMProvider):
        self.provider = provider
        self.model = getattr(provider, "model", type(provider).__name__)

    @property
    def audit_callback(self):
        return getattr(self.provider, "audit_callback", None)

    @audit_callback.setter
    def audit_callback(self, callback):
        if hasattr(self.provider, "audit_callback"):
            self.provider.audit_callback = callback

    def complete(self, system: str, prompt: str, json_mode: bool = False) -> str:
        system_result = mask_text(system)
        prompt_result = mask_text(prompt)
        # Each request has one namespace.  Avoid collisions when both fields
        # happen to contain a first sensitive value.
        offset = len(system_result.replacements)
        prompt_replacements = tuple(
            (f"[[PERSONAL_DATA_{index + offset:03d}]]", original)
            for index, (_, original) in enumerate(prompt_result.replacements, 1)
        )
        masked_prompt = prompt_result.masked_text
        for old, (new, _) in zip(
            (placeholder for placeholder, _ in prompt_result.replacements),
            prompt_replacements,
        ):
            masked_prompt = masked_prompt.replace(old, new)
        complete_masked = getattr(self.provider, "complete_masked", None)
        if complete_masked is not None:
            raw = complete_masked(
                system_result.masked_text,
                masked_prompt,
                json_mode,
            )
        else:
            raw = self.provider.complete(
                system_result.masked_text, masked_prompt, json_mode
            )
        # Responses can contain values from either part of the request.
        combined = dict(system_result.replacements + prompt_replacements)
        result = restore_text(raw, type(system_result)(
            system_result.status, system_result.masked_text,
            tuple(combined.items()), system_result.reason))
        if result.status.value != "ok":
            raise ValueError("provider response could not be safely restored")
        return result.text

    def embed(self, text: str) -> list[float]:
        return self.provider.embed(mask_text(text).masked_text)
