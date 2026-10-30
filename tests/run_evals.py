"""Run the offline golden-set evaluation for the reply pipeline.

Run from the repository root with:

    python -m tests.run_evals

The evaluator uses a temporary database, so it never changes mini_crm.db.
It also uses FakeLLMProvider, which makes this command deterministic and
does not require network access or an API key.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from app.db.database import initialize_database, open_database
from app.db.emails import get_email
from app.db.faqs import create_faq
from app.providers.llm_fake import FakeLLMProvider
from app.reply.pipeline import process_email


ROOT = Path(__file__).resolve().parents[1]
GOLDEN_FILE = ROOT / "tests" / "golden" / "emails.json"
FAQ_FILE = ROOT / "sample_data" / "faqs.json"


def _load_json(path: Path) -> list[dict[str, object]]:
    """Load a JSON list and fail early if the fixture has the wrong shape."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{path} must contain a list of objects")
    return value


def _make_database(database_path: Path, cases: list[dict[str, object]]) -> None:
    """Create the isolated database, approved FAQs, and one row per case."""
    initialize_database(database_path)

    for faq in _load_json(FAQ_FILE):
        create_faq(
            database_path,
            str(faq["question"]),
            str(faq["answer"]),
            approved=True,
        )

    # The evaluator does not need real customer records.  A generic sender
    # keeps the output private while still exercising the normal EmailRecord.
    with open_database(database_path) as connection:
        for case in cases:
            connection.execute(
                """
                INSERT INTO emails
                    (external_id, from_name, from_email, subject, body)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(case["id"]),
                    "Golden Customer",
                    "golden@example.invalid",
                    str(case["subject"]),
                    str(case["body"]),
                ),
            )


def evaluate() -> tuple[int, int, int, int, int]:
    """Run every case and return (total, exact, wrong, missed, errors)."""
    cases = _load_json(GOLDEN_FILE)
    total = len(cases)
    exact = 0
    wrong_drafts = 0
    missed_escalations = 0
    errors = 0

    # Some application database helpers keep SQLite connections alive until
    # process exit.  Ignore cleanup errors on Windows; the directory is still
    # temporary and is never the real application database.
    with tempfile.TemporaryDirectory(
        prefix="mini-crm-eval-", ignore_cleanup_errors=True
    ) as directory:
        database_path = Path(directory) / "evaluation.sqlite"
        _make_database(database_path, cases)
        provider = FakeLLMProvider()

        for row_number, case in enumerate(cases, start=1):
            email = get_email(database_path, row_number)
            if email is None:
                errors += 1
                continue

            try:
                result = process_email(email, database_path, provider)
            except Exception:
                # A provider or pipeline crash is an evaluation error, but
                # do not print the email body or other case data.
                errors += 1
                continue

            expected_status = str(case["expected_status"])
            expected_faq_id = case.get("expected_faq_id")
            actual_faq_id = result.faq.id if result.faq is not None else None
            case_is_exact = result.status == expected_status and (
                expected_status != "pending" or actual_faq_id == expected_faq_id
            )
            if case_is_exact:
                exact += 1
            else:
                errors += 1

            if result.status == "pending" and (
                expected_status == "escalated"
                or actual_faq_id != expected_faq_id
            ):
                wrong_drafts += 1
            if expected_status == "escalated" and result.status == "pending":
                missed_escalations += 1

    return total, exact, wrong_drafts, missed_escalations, errors


def main() -> int:
    """Print privacy-preserving metrics and return a shell-friendly exit code."""
    try:
        total, exact, wrong, missed, errors = evaluate()
    except Exception as exc:
        # The exception type is useful for a broken fixture, without exposing
        # any message content or personal data.
        print(f"Evaluation errors: 1 ({type(exc).__name__})")
        return 1

    accuracy = (exact / total * 100) if total else 0.0
    print(f"Total cases: {total}")
    print(f"Exact accuracy: {exact}/{total} ({accuracy:.1f}%)")
    print(f"Wrong-draft count: {wrong}")
    print(f"Missed-escalation count: {missed}")
    print(f"Errors: {errors}")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
