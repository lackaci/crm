"""Safe persistence and storage helpers for customer files."""

from dataclasses import dataclass
from pathlib import Path
import os
import sqlite3
import shutil

from .database import open_database

VALID_FILE_TYPES = frozenset(("invoice", "contract", "other"))
DEFAULT_STORAGE_ROOT = Path(__file__).resolve().parents[2] / "customer_files"


@dataclass(frozen=True)
class CustomerFile:
    """A file stored for a customer."""

    id: int
    customer_id: int
    filename: str
    file_type: str
    path: str
    created_at: str
    source_email_id: int | None = None


# A descriptive compatibility name for callers that prefer "record" terminology.
FileRecord = CustomerFile


def log_action(connection: object, actor: str, action: str, details: str) -> None:
    """Write one audit event using the caller's open transaction."""
    connection.execute(  # type: ignore[attr-defined]
        "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
        (actor, action, details),
    )


def store_file(
    database_path: Path,
    customer_id: int,
    source_path: Path,
    file_type: str,
    storage_root: Path | None = None,
    actor: str = "file-storage",
) -> CustomerFile:
    """Copy a source file into its customer folder and record the copy."""
    if isinstance(customer_id, bool) or not isinstance(customer_id, int) or customer_id <= 0:
        raise ValueError("Customer ID must be a positive integer.")
    clean_type = file_type.strip().lower()
    if clean_type not in VALID_FILE_TYPES:
        raise ValueError("File type must be invoice, contract, or other.")

    source = Path(source_path)
    if not source.exists():
        raise OSError(f"Source file does not exist: {source}")
    if not source.is_file():
        raise OSError(f"Source path is not a regular file: {source}")
    if not os.access(source, os.R_OK):
        raise OSError(f"Source file is not readable: {source}")
    filename = source.name
    if not filename:
        raise ValueError("Source file must have a filename.")

    root = Path(storage_root) if storage_root is not None else DEFAULT_STORAGE_ROOT
    customer_folder = root / f"customer_{customer_id}"
    destination = customer_folder / filename
    if destination.exists():
        raise ValueError(f"A file named {filename!r} already exists for this customer.")

    with open_database(database_path) as connection:
        customer = connection.execute(
            "SELECT id FROM customers WHERE id = ?", (customer_id,)
        ).fetchone()
        if customer is None:
            raise ValueError("Customer was not found.")

        customer_folder.mkdir(parents=True, exist_ok=True)
        try:
            # copy2 preserves useful metadata and never silently overwrites here.
            shutil.copy2(source, destination)
            relative_path = destination.relative_to(root).as_posix()
            cursor = connection.execute(
                """
                INSERT INTO files (customer_id, filename, file_type, path)
                VALUES (?, ?, ?, ?)
                """,
                (customer_id, filename, clean_type, relative_path),
            )
            file_id = int(cursor.lastrowid)
            log_action(
                connection,
                actor,
                "file_stored",
                f"file_id={file_id}; customer_id={customer_id}; filename={filename}",
            )
            row = connection.execute(
                """
                SELECT id, customer_id, filename, file_type, path, created_at,
                       source_email_id
                FROM files WHERE id = ?
                """,
                (file_id,),
            ).fetchone()
        except (OSError, sqlite3.Error):
            # Keep the filesystem and database in sync when an expected
            # storage or SQLite operation fails.  Do not catch programming
            # errors here: they should retain their normal traceback.
            try:
                destination.unlink()
            except OSError:
                # Preserve the original storage/database error if cleanup
                # itself cannot access the destination.
                pass
            raise
    if row is None:  # pragma: no cover - INSERT always returns a row
        raise RuntimeError("Stored file could not be read back.")
    return _file_from_row(row)


def add_file(
    database_path: Path,
    customer_id: int,
    source_path: Path,
    file_type: str,
    storage_root: Path | None = None,
    actor: str = "file-storage",
) -> CustomerFile:
    """Alias for :func:`store_file` for application-facing callers."""
    return store_file(database_path, customer_id, source_path, file_type, storage_root, actor)


def list_files(database_path: Path, customer_id: int) -> list[CustomerFile]:
    """Return a customer's stored files in insertion order."""
    if isinstance(customer_id, bool) or not isinstance(customer_id, int) or customer_id <= 0:
        raise ValueError("Customer ID must be a positive integer.")
    with open_database(database_path) as connection:
        rows = connection.execute(
            """
            SELECT id, customer_id, filename, file_type, path, created_at,
                   source_email_id
            FROM files WHERE customer_id = ? ORDER BY id
            """,
            (customer_id,),
        ).fetchall()
    return [_file_from_row(row) for row in rows]


def list_files_for_customer(database_path: Path, customer_id: int) -> list[CustomerFile]:
    """Alias for :func:`list_files`."""
    return list_files(database_path, customer_id)


def _file_from_row(row: object) -> CustomerFile:
    """Convert a SQLite row to the public dataclass."""
    return CustomerFile(
        id=int(row["id"]),  # type: ignore[index]
        customer_id=int(row["customer_id"]),  # type: ignore[index]
        filename=str(row["filename"]),  # type: ignore[index]
        file_type=str(row["file_type"]),  # type: ignore[index]
        path=str(row["path"]),  # type: ignore[index]
        created_at=str(row["created_at"]),  # type: ignore[index]
        source_email_id=(
            int(row["source_email_id"])
            if row["source_email_id"] is not None
            else None
        ),
    )
