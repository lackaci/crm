"""Run the offline golden-set evaluation for the reply pipeline.

Run from the repository root with:

    python -m tests.run_evals

The evaluator uses a temporary database, so it never changes mini_crm.db.
It also uses FakeLLMProvider, which makes this command deterministic and
does not require network access or an API key.
"""

from __future__ import annotations

import json
import argparse
import os
import sys
import tempfile
from pathlib import Path

from app.db.database import initialize_database, open_database
from app.db.emails import get_email
from app.db.faqs import add_faq, create_faq
from app.providers.llm_fake import FakeLLMProvider
from app.reply.pipeline import process_email


ROOT = Path(__file__).resolve().parents[1]
GOLDEN_FILE = ROOT / "tests" / "golden" / "emails.json"
FAQ_FILE = ROOT / "sample_data" / "faqs.json"


def _load_json(path: Path) -> list[dict[str, object]]:
    """Load a JSON list and fail early if the fixture has the wrong shape."""
    # PowerShell commonly writes UTF-8 JSON with a BOM.
    value = json.loads(path.read_text(encoding="utf-8-sig"))
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
    # Some golden cases include a lifecycle FAQ that deliberately has the
    # same wording as the case.  Seed it explicitly so the case proves that
    # an excluded best match is ignored, rather than merely being unrelated
    # to the approved sample FAQs.
    for case in cases:
        lifecycle_faq = case.get("lifecycle_faq")
        if isinstance(lifecycle_faq, dict):
            add_faq(
                database_path,
                str(lifecycle_faq["question"]),
                str(lifecycle_faq["answer"]),
                status=str(lifecycle_faq["status"]),
                actor="golden-eval",
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


def evaluate(
    provider_name: str = "mock", threshold: float | None = None
) -> dict[str, object]:
    """Run every case and return safety metrics.

    ``threshold`` is intentionally passed down to the pipeline rather than
    changing ``REPLY_MATCH_THRESHOLD``.  This lets tuning compare values in
    one process without changing the application's configured default.
    """
    cases = _load_json(GOLDEN_FILE)
    total = len(cases)
    exact = 0
    wrong_drafts = 0
    missed_escalations = 0
    errors = 0
    failures: list[dict[str, str]] = []

    # Some application database helpers keep SQLite connections alive until
    # process exit.  Ignore cleanup errors on Windows; the directory is still
    # temporary and is never the real application database.
    with tempfile.TemporaryDirectory(
        prefix="mini-crm-eval-", ignore_cleanup_errors=True
    ) as directory:
        database_path = Path(directory) / "evaluation.sqlite"
        _make_database(database_path, cases)
        # Mock is deliberately the default: it is deterministic and offline.
        if provider_name != "mock":
            raise RuntimeError("Only Mock evaluation is available offline")
        provider = FakeLLMProvider()

        for row_number, case in enumerate(cases, start=1):
            email = get_email(database_path, row_number)
            if email is None:
                errors += 1
                continue

            try:
                result = process_email(
                    email, database_path, provider, threshold=threshold
                )
            except Exception:
                # A provider or pipeline crash is an evaluation error, but
                # do not print the email body or other case data.
                errors += 1
                continue

            # expected_status is accepted for backwards compatibility with
            # the starter fixture; the committed fixture uses the M13 name.
            expected_status = str(case.get("expected", case.get("expected_status")))
            expected_status = "pending" if expected_status == "draft" else expected_status
            expected_status = "escalated" if expected_status == "escalate" else expected_status
            expected_faq_id = case.get("expected_faq_id")
            actual_faq_id = result.faq.id if result.faq is not None else None
            case_is_exact = result.status == expected_status and (
                expected_status != "pending" or actual_faq_id == expected_faq_id
            )
            if case_is_exact:
                exact += 1
            else:
                errors += 1
                reason = "status mismatch"
                if result.status == "pending" and actual_faq_id != expected_faq_id:
                    reason = "wrong faq_id"
                failures.append({"id": str(case["id"]), "reason": reason})

            if result.status == "pending" and (
                expected_status == "escalated"
                or actual_faq_id != expected_faq_id
            ):
                wrong_drafts += 1
            if expected_status == "escalated" and result.status == "pending":
                missed_escalations += 1

    return {
        "total": total,
        "exact": exact,
        "accuracy": round(exact / total * 100, 1) if total else 0.0,
        "wrong_draft": wrong_drafts,
        "wrong_faq": sum(
            1 for failure in failures if failure["reason"] == "wrong faq_id"
        ),
        "missed_faq": sum(
            1 for case in cases
            if str(case.get("expected", case.get("expected_status"))) == "draft"
            and any(f["id"] == str(case["id"]) for f in failures)
        ),
        "errors": errors,
        "failures": failures,
    }


def main() -> int:
    """Print privacy-preserving metrics and return a shell-friendly exit code."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("mock", "cloud"), default="mock")
    args = parser.parse_args()
    if args.provider == "cloud":
        if os.environ.get("MINI_CRM_MODE") not in {"Dry-run", "Live"}:
            print("Cloud evaluation refused: set MINI_CRM_MODE explicitly first.")
            return 1
        print("Estimated API calls: 2 per case.")
        if input("Type yes to continue: ").strip().lower() != "yes":
            print("Cloud evaluation cancelled.")
            return 1
        print("Cloud evaluation is not enabled by the offline evaluator.")
        return 1

    try:
        metrics = evaluate(args.provider)
    except Exception as exc:
        # The exception type is useful for a broken fixture, without exposing
        # any message content or personal data.
        print(f"Evaluation errors: 1 ({type(exc).__name__})")
        return 1

    result_path = ROOT / "tests" / "golden" / "last_run.json"
    result_path.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    print(f"Total cases: {metrics['total']}")
    print(f"Accuracy: {metrics['exact']}/{metrics['total']} ({metrics['accuracy']:.1f}%)")
    print(f"WRONG_DRAFT: {metrics['wrong_draft']}")
    print(f"WRONG_FAQ: {metrics['wrong_faq']}")
    print(f"MISSED_FAQ: {metrics['missed_faq']}")
    failures = ", ".join(
        f"{failure['id']} ({failure['reason']})"
        for failure in metrics["failures"]
    ) or "none"
    print(f"Failures: {failures}")
    return (
        0
        if metrics["wrong_draft"] == 0
        and metrics["wrong_faq"] == 0
        and metrics["errors"] == 0
        else 1
    )


if __name__ == "__main__":
    sys.exit(main())
