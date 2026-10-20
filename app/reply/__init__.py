"""Safe, FAQ-only reply generation."""

from .pipeline import DraftResult, process_email

__all__ = ["DraftResult", "process_email"]
