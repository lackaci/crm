"""Focused offline tests for the M15 candidate exporter."""

import json

import pytest

from app.db.database import initialize_database, open_database
from tools.export_for_golden import export_candidates


def test_export_masks_sender_data_and_does_not_touch_golden_set(tmp_path):
    database = tmp_path / "imported.sqlite"
    output = tmp_path / "candidates.json"
    initialize_database(database)
    with open_database(database) as connection:
        connection.execute(
            """
            INSERT INTO emails
                (provider, from_name, from_email, subject, body)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "gmail",
                "Ada Lovelace",
                "ada@example.test",
                "Address update",
                "Please send it to Ada Lovelace at 12 Example Street.",
            ),
        )

    candidates = export_candidates(database, output)

    assert len(candidates) == 1
    text = output.read_text(encoding="utf-8")
    assert "Ada Lovelace" not in text
    assert "ada@example.test" not in text
    assert "[[PERSONAL_DATA_" in text
    assert json.loads(text)[0]["expected"] == "review"


def test_export_requires_a_source_database(tmp_path):
    with pytest.raises(FileNotFoundError, match="Source database was not found"):
        export_candidates(tmp_path / "missing.sqlite", tmp_path / "candidates.json")


def test_export_ignores_mock_sample_messages(tmp_path):
    database = tmp_path / "imported.sqlite"
    output = tmp_path / "candidates.json"
    initialize_database(database)
    with open_database(database) as connection:
        connection.execute(
            """
            INSERT INTO emails
                (provider, from_name, from_email, subject, body)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("mock", "Sample", "sample@example.test", "Hi", "Hello"),
        )

    assert export_candidates(database, output) == []


@pytest.mark.parametrize(
    "body, message",
    [
        ("Please ask John Smith to call me.", "third-party name"),
        ("请联系王小明。", "non-ASCII personal text"),
    ],
)
def test_export_refuses_unrecognized_personal_data(tmp_path, body, message):
    database = tmp_path / "imported.sqlite"
    output = tmp_path / "candidates.json"
    initialize_database(database)
    with open_database(database) as connection:
        connection.execute(
            """
            INSERT INTO emails
                (provider, from_name, from_email, subject, body)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("gmail", "Test Sender", "sender@example.test", "Question", body),
        )

    with pytest.raises(ValueError, match=message):
        export_candidates(database, output)
    assert not output.exists()


@pytest.mark.parametrize(
    "body",
    [
        "Send the parcel to 42 Rue de Paris.",
        "My address is PO Box 77.",
        "I live in 4B, 12 Straße Berlin.",
        "Meet me at Apartment 9B.",
    ],
)
def test_export_redacts_address_variants(tmp_path, body):
    database = tmp_path / "imported.sqlite"
    output = tmp_path / "candidates.json"
    initialize_database(database)
    with open_database(database) as connection:
        connection.execute(
            """
            INSERT INTO emails
                (provider, from_name, from_email, subject, body)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("gmail", "Test Sender", "sender@example.test", "Question", body),
        )

    export_candidates(database, output)
    exported = output.read_text(encoding="utf-8")
    assert body not in exported
    assert "[[PERSONAL_DATA_" in exported
