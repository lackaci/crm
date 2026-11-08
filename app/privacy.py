"""Small, deterministic privacy boundary for text sent to language models.

The original values live only in a request-scoped result.  Callers should pass
that result directly to :func:`restore_text`; it is deliberately not a
database or logging object.
"""

from dataclasses import dataclass
from enum import Enum
import os
import re
from typing import Iterable


class PrivacyStatus(str, Enum):
    OK = "ok"
    FAILED = "failed"


@dataclass(frozen=True)
class MaskingResult:
    status: PrivacyStatus
    masked_text: str
    # A tuple prevents accidental mutation and keeps the request scope clear.
    replacements: tuple[tuple[str, str], ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class RestorationResult:
    status: PrivacyStatus
    text: str
    reason: str | None = None


_PLACEHOLDER = re.compile(r"\[\[PERSONAL_DATA_\d{3}\]\]")
_MALFORMED_PLACEHOLDER = re.compile(
    r"\[\[PERSONAL_DATA_(?!\d{3}\]\])[^\]]*\]\]"
)
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[A-Z0-9]{2,4}){3,7}\b", re.I)
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_EMAIL = re.compile(r"(?<![\w.+-])[\w.!#$%&'*+/=?^`{|}~-]+@[\w.-]+\.[A-Za-z]{2,63}\b", re.I)
_DEFAULT_IDS = (
    re.compile(r"\bID-\d{6,12}\b", re.I),
    re.compile(r"\b[A-Z]{2}-\d{6,12}\b", re.I),
)


def _configured_patterns(patterns: Iterable[str | re.Pattern[str]] | None):
    if patterns is not None:
        return tuple(re.compile(pattern, re.I) if isinstance(pattern, str) else pattern
                     for pattern in patterns)
    configured = os.getenv("MINI_CRM_ID_PATTERNS", "")
    if configured:
        return tuple(re.compile(pattern, re.I) for pattern in configured.split(",") if pattern)
    return _DEFAULT_IDS


def mask_text(text: str, id_patterns: Iterable[str | re.Pattern[str]] | None = None) -> MaskingResult:
    """Replace emails, cards, IBANs and configured IDs with stable placeholders."""
    if not isinstance(text, str):
        return MaskingResult(PrivacyStatus.FAILED, "", reason="text is not valid")
    candidates: list[tuple[int, int, str]] = []
    for match in _CARD.finditer(text):
        # Formatting is enough to be conservative.  A checksum failure is
        # not permission to send a card-like value to a provider.
        candidates.append((match.start(), match.end(), match.group()))
    for match in _IBAN.finditer(text):
        candidates.append((match.start(), match.end(), match.group()))
    for match in _EMAIL.finditer(text):
        candidates.append((match.start(), match.end(), match.group()))
    for pattern in _configured_patterns(id_patterns):
        candidates.extend((match.start(), match.end(), match.group())
                          for match in pattern.finditer(text))
    # Longest first at the same position, then text order.  Overlaps are
    # resolved conservatively: an already covered value wins.
    selected: list[tuple[int, int, str]] = []
    for candidate in sorted(candidates, key=lambda item: (item[0], -(item[1] - item[0]))):
        if any(candidate[0] < end and start < candidate[1]
               for start, end, _ in selected):
            continue
        selected.append(candidate)
    selected.sort()
    pieces = []
    replacements = []
    cursor = 0
    for number, (start, end, original) in enumerate(selected, 1):
        placeholder = f"[[PERSONAL_DATA_{number:03d}]]"
        pieces.extend((text[cursor:start], placeholder))
        replacements.append((placeholder, original))
        cursor = end
    pieces.append(text[cursor:])
    return MaskingResult(PrivacyStatus.OK, "".join(pieces), tuple(replacements))


def restore_text(text: str, masking: MaskingResult) -> RestorationResult:
    """Restore only once-created placeholders; reject ambiguity or tampering."""
    if masking.status is not PrivacyStatus.OK or not isinstance(text, str):
        return RestorationResult(PrivacyStatus.FAILED, "", "masking failed")
    mapping = dict(masking.replacements)
    found = _PLACEHOLDER.findall(text)
    if _MALFORMED_PLACEHOLDER.search(text) or any(item not in mapping for item in found):
        return RestorationResult(PrivacyStatus.FAILED, "", "unknown placeholder")
    if len(found) != len(set(found)):
        return RestorationResult(PrivacyStatus.FAILED, "", "duplicate placeholder")
    restored = text
    for placeholder, original in masking.replacements:
        restored = restored.replace(placeholder, original)
    return RestorationResult(PrivacyStatus.OK, restored)


def masked_preview(system: str, prompt: str) -> tuple[str, str]:
    """Return only what may be shown in a debug 'what we send' preview."""
    return mask_text(system).masked_text, mask_text(prompt).masked_text
