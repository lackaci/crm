"""SQLite persistence helpers for frequently asked questions."""

from dataclasses import dataclass
from pathlib import Path

from .database import log_action, open_database

FAQ_STATUSES = frozenset(("draft", "approved", "inactive"))


@dataclass(frozen=True)
class FAQEntry:
    """A question and answer that can be used by the reply pipeline."""

    id: int
    question: str
    answer: str
    approved: bool
    created_at: str
    status: str = "draft"
    approved_at: str | None = None
    updated_at: str | None = None


def _validate_text(question: str, answer: str) -> tuple[str, str]:
    question, answer = question.strip(), answer.strip()
    if not question or not answer:
        raise ValueError("FAQ question and answer are required.")
    return question, answer


def _validate_status(status: str) -> str:
    if status not in FAQ_STATUSES:
        raise ValueError(f"Invalid FAQ status: {status!r}.")
    return status


def add_faq(
    database_path: Path,
    question: str,
    answer: str,
    approved: bool = False,
    actor: str = "faq",
    status: str | None = None,
) -> int:
    """Add an FAQ; the old ``approved`` argument remains supported."""
    question, answer = _validate_text(question, answer)
    chosen_status = _validate_status(status or ("approved" if approved else "draft"))
    approved_value = int(chosen_status == "approved")
    with open_database(database_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO faq_entries
                (question, answer, approved, status, approved_at, updated_at)
            VALUES (?, ?, ?, ?, CASE WHEN ? = 'approved' THEN CURRENT_TIMESTAMP END,
                    CURRENT_TIMESTAMP)
            """,
            (question, answer, approved_value, chosen_status, chosen_status),
        )
        faq_id = int(cursor.lastrowid)
    log_action(database_path, actor, "faq_created", f"faq_id={faq_id}; status={chosen_status}")
    return faq_id


def create_faq(
    database_path: Path,
    question: str,
    answer: str,
    approved: bool = False,
    actor: str = "faq",
) -> int:
    """Backward-compatible alias for :func:`add_faq`."""
    return add_faq(database_path, question, answer, approved, actor)


def update_faq(
    database_path: Path,
    faq_id: int,
    question: str,
    answer: str,
    actor: str = "faq",
) -> None:
    """Edit an FAQ and require re-approval after every edit."""
    question, answer = _validate_text(question, answer)
    with open_database(database_path) as connection:
        cursor = connection.execute(
            """
            UPDATE faq_entries
            SET question = ?, answer = ?, approved = 0, status = 'draft',
                approved_at = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (question, answer, faq_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("FAQ was not found.")
    log_action(database_path, actor, "faq_updated", f"faq_id={faq_id}; status=draft")


def approve_faq(database_path: Path, faq_id: int, actor: str = "faq") -> None:
    """Mark an FAQ approved and record the approval time."""
    with open_database(database_path) as connection:
        cursor = connection.execute(
            """
            UPDATE faq_entries
            SET approved = 1, status = 'approved',
                approved_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (faq_id,),
        )
        if cursor.rowcount != 1:
            raise ValueError("FAQ was not found.")
    log_action(database_path, actor, "faq_approved", f"faq_id={faq_id}; status=approved")


def deactivate_faq(database_path: Path, faq_id: int, actor: str = "faq") -> None:
    """Hide an FAQ from matching without deleting its history."""
    with open_database(database_path) as connection:
        cursor = connection.execute(
            """
            UPDATE faq_entries
            SET approved = 0, status = 'inactive', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (faq_id,),
        )
        if cursor.rowcount != 1:
            raise ValueError("FAQ was not found.")
    log_action(database_path, actor, "faq_deactivated", f"faq_id={faq_id}; status=inactive")


def list_faqs(database_path: Path, status: str | None = None) -> list[FAQEntry]:
    """List FAQs, optionally restricted to one valid lifecycle status."""
    if status is not None:
        _validate_status(status)
    with open_database(database_path) as connection:
        if status is None:
            rows = connection.execute(
                "SELECT id, question, answer, approved, status, approved_at, "
                "updated_at, created_at FROM faq_entries ORDER BY id"
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT id, question, answer, approved, status, approved_at, "
                "updated_at, created_at FROM faq_entries WHERE status = ? ORDER BY id",
                (status,),
            ).fetchall()
    return [_faq_from_row(row) for row in rows]


def list_approved_faqs(database_path: Path) -> list[FAQEntry]:
    """Return only approved FAQs used in generated replies."""
    return list_faqs(database_path, "approved")


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
        status=str(row["status"]),  # type: ignore[index]
        approved_at=row["approved_at"],  # type: ignore[index]
        updated_at=str(row["updated_at"]),  # type: ignore[index]
    )
