"""SQLite persistence helpers for frequently asked questions."""

from dataclasses import dataclass
from pathlib import Path

from .database import open_database


@dataclass(frozen=True)
class FAQEntry:
    """A question and answer that can be used by the reply pipeline."""

    id: int
    question: str
    answer: str
    approved: bool
    created_at: str


def create_faq(
    database_path: Path,
    question: str,
    answer: str,
    approved: bool = False,
    actor: str = "faq",
) -> int:
    """Create an FAQ and return its database ID."""
    question = question.strip()
    answer = answer.strip()
    if not question or not answer:
        raise ValueError("FAQ question and answer are required.")

    with open_database(database_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO faq_entries (question, answer, approved)
            VALUES (?, ?, ?)
            """,
            (question, answer, int(approved)),
        )
        connection.execute(
            "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
            (actor, "faq_created", f"faq_id={cursor.lastrowid}"),
        )
        return int(cursor.lastrowid)


def add_faq(
    database_path: Path,
    question: str,
    answer: str,
    approved: bool = False,
    actor: str = "faq",
) -> int:
    """Backward-friendly name for :func:`create_faq`."""
    return create_faq(database_path, question, answer, approved, actor)


def list_approved_faqs(database_path: Path) -> list[FAQEntry]:
    """Return only FAQs approved for use in generated replies."""
    with open_database(database_path) as connection:
        rows = connection.execute(
            """
            SELECT id, question, answer, approved, created_at
            FROM faq_entries
            WHERE approved = 1
            ORDER BY id
            """
        ).fetchall()
    return [_faq_from_row(row) for row in rows]


def load_faqs(database_path: Path) -> list[FAQEntry]:
    """Load the approved FAQ set used by reply-generation code."""
    return list_approved_faqs(database_path)


def _faq_from_row(row: object) -> FAQEntry:
    """Convert a SQLite row to the public FAQ dataclass."""
    return FAQEntry(
        id=int(row["id"]),  # type: ignore[index]
        question=str(row["question"]),  # type: ignore[index]
        answer=str(row["answer"]),  # type: ignore[index]
        approved=bool(row["approved"]),  # type: ignore[index]
        created_at=str(row["created_at"]),  # type: ignore[index]
    )
