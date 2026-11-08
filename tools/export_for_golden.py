"""Export imported test mail as a privacy-safe review candidate file.

This tool only reads SQLite.  It never calls an email provider and it never
changes the golden set automatically.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from app.privacy import PrivacyStatus, mask_text


_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d ()-]{6,}\d)(?!\w)")
_IDENTIFIER = re.compile(r"\b(?:ID-\d{6,12}|[A-Z]{2}-\d{6,12})\b", re.I)
# These patterns intentionally cover more than the usual US street format.
# Anything that looks like an address is replaced before the final checks.
_ADDRESS = re.compile(
    r"(?:"
    r"\b(?:p\.?\s*o\.?\s*box|postfach|casella\s+postale)\s*#?\s*\d+\b"
    r"|"
    r"\b\d{1,6}\s+(?:(?:street|st|road|rd|avenue|ave|lane|ln|"
    r"boulevard|blvd|strasse|straße|rue|via|道|路)\b[^\n,;]{0,80}"
    r"|[^\n,;]{1,80}?\s+(?:street|st|road|rd|avenue|ave|lane|ln|"
    r"boulevard|blvd|strasse|straße|rue|via|道|路)\b)"
    r"|"
    r"\b(?:apt|apartment|unit|suite|flat|floor|#)\s*[A-Z0-9-]+\b"
    r")",
    re.I,
)
_LIKELY_NAME = re.compile(
    r"\b(?:mr|mrs|ms|miss|dr|prof)\.?\s+[A-ZÀ-ÖØ-Ý][\wÀ-ÿ'-]+"
    r"|"
    r"\b[A-Z][a-z]+(?:[-'][A-Z][a-z]+)?\s+[A-Z][a-z]+(?:[-'][A-Z][a-z]+)?\b"
)
_NON_ASCII = re.compile(r"[^\x00-\x7f]")


def _masked(value: str, personal_values: tuple[str, ...]) -> str:
    """Mask known and recognizable personal data, then fail closed."""
    patterns = [_EMAIL, _PHONE, _ADDRESS, _IDENTIFIER]
    patterns.extend(re.escape(value) for value in personal_values if value)
    result = mask_text(value, patterns)
    if result.status is not PrivacyStatus.OK:
        raise ValueError("Privacy masking failed for an imported email.")
    sanitized = result.masked_text
    if _EMAIL.search(sanitized):
        raise ValueError("Privacy export refused: an email address remained.")
    if _ADDRESS.search(sanitized):
        raise ValueError("Privacy export refused: a likely address remained.")
    if _LIKELY_NAME.search(sanitized):
        raise ValueError(
            "Privacy export refused: a likely third-party name remained."
        )
    if _NON_ASCII.search(sanitized):
        raise ValueError(
            "Privacy export refused: non-ASCII personal text remained."
        )
    return sanitized


def export_candidates(database_path: Path, output_path: Path) -> list[dict[str, Any]]:
    """Read imported messages and write review-only candidate records."""
    if not database_path.is_file():
        raise FileNotFoundError(
            f"Source database was not found: {database_path}. "
            "Run the owner-approved Dry-run import first."
        )
    if output_path.name.lower() == "emails.json":
        raise ValueError(
            "Refusing to write emails.json; candidates require owner review first."
        )

    import sqlite3

    try:
        with sqlite3.connect(database_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT id, from_name, from_email, subject, body
                FROM emails
                WHERE provider = ?
                ORDER BY id
                """,
                ("gmail",),
            ).fetchall()
    except sqlite3.Error as error:
        raise RuntimeError(
            f"Could not read imported test emails from {database_path}: {error}"
        ) from error

    candidates: list[dict[str, Any]] = []
    for number, row in enumerate(rows, 1):
        personal = (str(row["from_name"]), str(row["from_email"]))
        candidates.append(
            {
                "id": f"m15-{number:03d}",
                "category": "review",
                "from_name": _masked(personal[0], personal),
                "subject": _masked(str(row["subject"]), personal),
                "body": _masked(str(row["body"]), personal),
                "expected": "review",
                "notes": "Owner must review this candidate before any golden-set change.",
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(candidates, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return candidates


def main() -> int:
    """Run the explicitly local, read-only export."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, help="local imported test SQLite database")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tests/golden/candidates.json"),
        help="review-only output path (default: tests/golden/candidates.json)",
    )
    args = parser.parse_args()
    import os

    database = args.database or (
        Path(os.environ["MINI_CRM_DATABASE_PATH"])
        if os.environ.get("MINI_CRM_DATABASE_PATH")
        else None
    )
    if database is None:
        parser.error(
            "a source database is required; pass --database or set "
            "MINI_CRM_DATABASE_PATH"
        )
    try:
        candidates = export_candidates(database, args.output)
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        parser.error(str(error))
    print(f"Wrote {len(candidates)} review-only candidates to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
