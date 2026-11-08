"""Focused tests for the offline threshold sweep."""

from pathlib import Path

from app.db.database import initialize_database, open_database
from app.db.emails import EmailRecord
from app.db.faqs import add_faq, list_approved_faqs
from app.providers.llm_fake import FakeLLMProvider
from app.reply.matcher import find_best_faq
from app.reply.pipeline import process_email
from scripts.threshold_sweep import THRESHOLDS, run_sweep
from tests.run_evals import evaluate


def test_explicit_threshold_controls_escalation(tmp_path: Path) -> None:
    """A score below the supplied limit escalates; the score itself is accepted."""
    database_path = tmp_path / "threshold.sqlite"
    initialize_database(database_path)
    add_faq(
        database_path,
        "What are your opening hours?",
        "We are open on weekdays.",
        approved=True,
    )
    with open_database(database_path) as connection:
        connection.execute(
            "INSERT INTO emails (id, from_name, from_email, subject, body) "
            "VALUES (1, 'Ada', 'ada@example.invalid', 'Opening hours', "
            "'When are you open?')"
        )
    email = EmailRecord(
        1, None, "one", "Ada", "ada@example.invalid",
        "Opening hours", "When are you open?", None,
    )
    match = find_best_faq(
        email,
        list_approved_faqs(database_path),
        FakeLLMProvider(),
    )

    assert process_email(
        email, database_path, FakeLLMProvider(), threshold=match.score + 0.001
    ).status == "escalated"
    assert process_email(
        email, database_path, FakeLLMProvider(), threshold=match.score
    ).status == "pending"


def test_evaluator_accepts_explicit_threshold_without_changing_default() -> None:
    default = evaluate()
    explicit_default = evaluate(threshold=0.60)

    assert explicit_default == default


def test_sweep_uses_all_required_thresholds_and_metrics() -> None:
    results = run_sweep()

    assert THRESHOLDS == (0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90)
    assert [threshold for threshold, _ in results] == list(THRESHOLDS)
    assert all(
        {"wrong_draft", "wrong_faq", "missed_faq", "errors"} <= metrics.keys()
        for _, metrics in results
    )
    assert all(metrics["errors"] == 0 for _, metrics in results)
