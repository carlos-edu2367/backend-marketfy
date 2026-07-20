from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from infra.integrations.mailgun import MailgunEmailGateway, EmailDeliveryError


@pytest.mark.asyncio
async def test_send_invoice_available_posts_to_mailgun():
    gw = MailgunEmailGateway(api_key="k", domain="mg.x.com", from_email="n@x.com", from_name="Marketfy")

    class Resp:
        status_code = 200
        text = "ok"

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.post.return_value = Resp()

    with patch("infra.integrations.mailgun.httpx.AsyncClient", return_value=mock_client):
        await gw.send_invoice_available(
            to_email="c@x.com", to_name="Cliente", amount="50.00",
            due_date="25/07/2026", checkout_url="https://pay/x",
        )
    mock_client.post.assert_awaited_once()
    _, kwargs = mock_client.post.call_args
    assert kwargs["auth"] == ("api", "k")
    assert "c@x.com" in kwargs["data"]["to"]


@pytest.mark.asyncio
async def test_send_raises_on_non_2xx():
    gw = MailgunEmailGateway(api_key="k", domain="mg.x.com", from_email="n@x.com", from_name="Marketfy")

    class Resp:
        status_code = 500
        text = "err"

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.post.return_value = Resp()

    with patch("infra.integrations.mailgun.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(EmailDeliveryError):
            await gw.send_invoice_available(
                to_email="c@x.com", to_name="Cliente", amount="50.00",
                due_date="25/07/2026", checkout_url="https://pay/x",
            )
