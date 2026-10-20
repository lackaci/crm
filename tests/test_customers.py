"""Tests for customer persistence."""

import sqlite3

import pytest

from app.db.customers import Customer, add_customer, list_customers, update_customer
from app.db.database import initialize_database


@pytest.fixture
def database_path(tmp_path):
    path = tmp_path / "test.db"
    initialize_database(path)
    return path


def test_add_and_list_customer(database_path):
    customer_id = add_customer(database_path, "Ada Lovelace", "ada@example.com")

    customers = list_customers(database_path)

    assert customers == [Customer(customer_id, "Ada Lovelace", "ada@example.com", "", "")]


def test_update_customer_persists_changes(database_path):
    customer_id = add_customer(
        database_path, "Old Name", "old@example.com", "111", "Old notes"
    )

    update_customer(
        database_path, customer_id, "New Name", "new@example.com", "222", "New notes"
    )

    customer = list_customers(database_path)[0]
    assert (customer.name, customer.email, customer.phone, customer.notes) == (
        "New Name",
        "new@example.com",
        "222",
        "New notes",
    )


def test_duplicate_email_is_rejected(database_path):
    add_customer(database_path, "First", "same@example.com")

    with pytest.raises(sqlite3.IntegrityError):
        add_customer(database_path, "Second", "same@example.com")
