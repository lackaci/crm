"""Regression check for the offline golden evaluation."""

import socket

import pytest

from tests.run_evals import evaluate


def test_no_real_network() -> None:
    """The M12 autouse fixture must block real socket connections."""
    connection = socket.socket()
    try:
        with pytest.raises(AssertionError, match="real network"):
            connection.connect(("example.invalid", 80))
    finally:
        connection.close()


def test_golden_eval_has_no_dangerous_failures() -> None:
    metrics = evaluate()
    assert metrics["wrong_draft"] == 0
    assert metrics["wrong_faq"] == 0
    assert metrics["errors"] == 0
