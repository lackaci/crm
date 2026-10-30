"""Privacy-preserving provider audit sinks."""

import json
import sqlite3
from pathlib import Path
from typing import Callable


SYNC_AUDIT_FIELDS = frozenset({"provider", "operation", "count", "outcome", "error_category"})


def database_sync_audit_callback(database_path: Path) -> Callable[[dict], None]:
    """Return a sink that stores only non-sensitive synchronization metadata."""
    def record(metadata: dict) -> None:
        safe = {
            key: metadata.get(key)
            for key in SYNC_AUDIT_FIELDS
            if key in metadata
        }
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "INSERT INTO audit_log(actor, action, details) VALUES (?, ?, ?)",
                ("provider", "email_sync", json.dumps(safe, sort_keys=True)),
            )
    return record


def database_audit_callback(database_path: Path) -> Callable[[dict], None]:
    def record(metadata: dict) -> None:
        safe = {key: metadata.get(key) for key in (
            "provider", "model", "prompt_version", "faq_id", "prompt_tokens",
            "completion_tokens", "total_tokens", "cost", "outcome",
            "error_category", "error_type", "retry_count")}
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "INSERT INTO audit_log(actor, action, details) VALUES (?, ?, ?)",
                ("provider", "llm_call", json.dumps(safe, sort_keys=True)))
    return record
