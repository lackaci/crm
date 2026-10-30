import pytest

from app.providers.email_base import EmailDeliveryError
from app.providers.email_mock import MockEmailProvider


def test_mock_send_is_disabled_without_output(capsys):
    with pytest.raises(EmailDeliveryError, match="disabled"):
        MockEmailProvider().send_email(
            "customer@example.com", "Private subject", "Private customer body"
        )
    assert capsys.readouterr().out == ""
