"""Focused tests for safe customer-file storage."""

import sqlite3

import pytest

from app.db.customers import add_customer
from app.db.database import initialize_database, open_database
from app.db.files import CustomerFile, list_files, store_file


@pytest.fixture
def database_path(tmp_path):
    path = tmp_path / "files.db"
    initialize_database(path)
    return path


@pytest.fixture
def customer(database_path):
    return add_customer(database_path, "Ada Lovelace", "ada@example.com")


@pytest.mark.parametrize("file_type", ["invoice", "contract", "other"])
def test_each_classification_is_stored(database_path, customer, tmp_path, file_type):
    source = tmp_path / f"{file_type}.txt"
    source.write_text("content", encoding="utf-8")

    stored = store_file(database_path, customer, source, file_type, tmp_path / "stored")

    assert isinstance(stored, CustomerFile)
    assert stored.file_type == file_type
    assert list_files(database_path, customer) == [stored]


def test_file_is_placed_in_customer_folder(database_path, customer, tmp_path):
    source = tmp_path / "statement.pdf"
    source.write_bytes(b"PDF")
    root = tmp_path / "customer-files"

    stored = store_file(database_path, customer, source, "invoice", root)

    assert (root / stored.path).read_bytes() == b"PDF"
    assert (root / f"customer_{customer}" / "statement.pdf").exists()


def test_duplicate_filename_does_not_overwrite(database_path, customer, tmp_path):
    source = tmp_path / "same.txt"
    source.write_text("first", encoding="utf-8")
    root = tmp_path / "stored"
    store_file(database_path, customer, source, "other", root)
    source.write_text("second", encoding="utf-8")

    with pytest.raises(ValueError, match="already exists"):
        store_file(database_path, customer, source, "other", root)

    assert (root / f"customer_{customer}" / "same.txt").read_text() == "first"
    assert len(list_files(database_path, customer)) == 1


def test_missing_source_is_rejected(database_path, customer, tmp_path):
    with pytest.raises(OSError, match="does not exist"):
        store_file(database_path, customer, tmp_path / "missing.txt", "other")


def test_file_storage_writes_audit_record(database_path, customer, tmp_path):
    source = tmp_path / "contract.docx"
    source.write_text("terms", encoding="utf-8")

    stored = store_file(
        database_path, customer, source, "contract", tmp_path / "stored", actor="tester"
    )

    with open_database(database_path) as connection:
        audit = connection.execute(
            "SELECT actor, action, details FROM audit_log WHERE action = ?",
            ("file_stored",),
        ).fetchone()
    assert audit["actor"] == "tester"
    assert str(stored.id) in audit["details"]


def test_database_failure_removes_partially_copied_file(
    database_path, customer, tmp_path, monkeypatch
):
    """A failed audit write must not leave an untracked file behind."""
    source = tmp_path / "failed.txt"
    source.write_text("content", encoding="utf-8")
    root = tmp_path / "stored"

    def fail_audit(*args, **kwargs):
        raise sqlite3.OperationalError("simulated audit failure")

    monkeypatch.setattr("app.db.files.log_action", fail_audit)

    with pytest.raises(sqlite3.OperationalError, match="simulated audit failure"):
        store_file(database_path, customer, source, "other", root)

    assert not (root / f"customer_{customer}" / source.name).exists()
    assert list_files(database_path, customer) == []
