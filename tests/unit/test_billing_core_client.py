import asyncio
import os
import sys
from datetime import datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from infra.clients.billing_core_client import (
    BillingCoreClient,
    BillingCoreAuthError,
    BillingCoreValidationError,
    BillingCoreUnavailableError,
    BillingCoreRateLimitError,
    BillingCoreError,
)
from infra.config.settings import get_settings

settings = get_settings()


@pytest.fixture(autouse=True)
def force_enabled_billing_core():
    """Força BILLING_CORE_ENABLED=True nos testes do cliente."""
    with patch.object(settings, "BILLING_CORE_ENABLED", True), \
         patch.object(settings, "BILLING_CORE_BASE_URL", "http://billing-core:8000"), \
         patch.object(settings, "BILLING_CORE_API_KEY", "mock-api-key"), \
         patch.object(settings, "BILLING_CORE_SYSTEM", "marketfy"):
        yield


@pytest.mark.asyncio
async def test_create_customer_success():
    client = BillingCoreClient()
    mock_response = httpx.Response(201, json={"provider_customer_id": "cus_12345"})

    with patch("httpx.AsyncClient.request", return_value=mock_response) as mock_request:
        res = await client.create_customer(
            nome_completo="João Silva",
            email="joao@example.com",
            system_customer_id="user-123",
            system="marketfy",
            cpf="12345678901",
        )
        assert res["provider_customer_id"] == "cus_12345"
        mock_request.assert_called_once()
        args, kwargs = mock_request.call_args
        assert kwargs["json"]["nome_completo"] == "João Silva"
        assert kwargs["json"]["cpf"] == "12345678901"
        assert "cnpj" not in kwargs["json"]


@pytest.mark.asyncio
async def test_create_customer_idempotency_returns_same():
    client = BillingCoreClient()
    # BC retorna 200/201 com o ID existente se já cadastrado
    mock_response = httpx.Response(200, json={"provider_customer_id": "cus_already_exists"})

    with patch("httpx.AsyncClient.request", return_value=mock_response):
        res = await client.create_customer(
            nome_completo="João Silva",
            email="joao@example.com",
            system_customer_id="user-123",
            system="marketfy",
            cnpj="12345678000190",
        )
        assert res["provider_customer_id"] == "cus_already_exists"


@pytest.mark.asyncio
async def test_create_customer_requires_cpf_or_cnpj_exclusively():
    client = BillingCoreClient()

    # Sem CPF e sem CNPJ
    with pytest.raises(ValueError, match="Deve fornecer cpf ou cnpj"):
        await client.create_customer(
            nome_completo="João Silva",
            email="joao@example.com",
            system_customer_id="user-123",
            system="marketfy",
        )

    # Com ambos
    with pytest.raises(ValueError, match="Forneça apenas cpf ou cnpj, não ambos"):
        await client.create_customer(
            nome_completo="João Silva",
            email="joao@example.com",
            system_customer_id="user-123",
            system="marketfy",
            cpf="12345678901",
            cnpj="12345678000190",
        )


@pytest.mark.asyncio
async def test_create_payment_posts_checkout_payload_with_idempotency():
    client = BillingCoreClient()
    mock_response = httpx.Response(202, json={"job_id": "job-abc-123", "message": "created"})

    with patch("httpx.AsyncClient.request", return_value=mock_response) as mock_request:
        res = await client.create_payment(
            value="72.00",
            description="Créditos NF-e",
            system="marketfy",
            system_payment_id="pack-100",
            webhook_link="https://marketfy/callback",
            idempotency_key="my-idempotency-key",
            minutes_to_expire=30,
            items=[{
                "external_reference": "pack-100",
                "name": "100 créditos NF-e",
                "description": "Créditos para emissão fiscal",
                "quantity": 1,
                "value": "72.00",
            }],
            success_url="https://app.marketfy.test/billing/success",
            cancel_url="https://app.marketfy.test/billing/cancel",
            expired_url="https://app.marketfy.test/billing/expired",
        )
        assert res["job_id"] == "job-abc-123"
        mock_request.assert_called_once()
        args, kwargs = mock_request.call_args
        headers = kwargs["headers"]
        assert headers["X-System"] == "marketfy"
        assert headers["X-API-Key"] == "mock-api-key"
        assert headers["Idempotency-Key"] == "my-idempotency-key"
        assert args[0] == "POST"
        assert args[1].endswith("/v1/payments")
        assert kwargs["json"] == {
            "value": "72.00",
            "description": "Créditos NF-e",
            "system": "marketfy",
            "system_payment_id": "pack-100",
            "webhook_link": "https://marketfy/callback",
            "minutes_to_expire": 30,
            "items": [{
                "external_reference": "pack-100",
                "name": "100 créditos NF-e",
                "description": "Créditos para emissão fiscal",
                "quantity": 1,
                "value": "72.00",
            }],
            "success_url": "https://app.marketfy.test/billing/success",
            "cancel_url": "https://app.marketfy.test/billing/cancel",
            "expired_url": "https://app.marketfy.test/billing/expired",
        }


@pytest.mark.asyncio
async def test_create_payment_mock_when_billing_core_disabled():
    with patch.object(settings, "BILLING_CORE_ENABLED", False):
        client = BillingCoreClient()

    res = await client.create_payment(
        value="41.99",
        description="Creditos NF-e - pack_100",
        system="marketfy",
        system_payment_id="pkg_100_abc",
        webhook_link="https://marketfy/callback",
        idempotency_key="pkg_100_abc",
        minutes_to_expire=30,
        items=[],
        success_url="https://app.marketfy.test/billing/success",
        cancel_url="https://app.marketfy.test/billing/cancel",
        expired_url="https://app.marketfy.test/billing/expired",
    )

    assert res["job_id"].startswith("job_mock_")
    assert "Checkout creation accepted" in res["message"]


@pytest.mark.asyncio
async def test_get_job_success():
    client = BillingCoreClient()
    mock_response = httpx.Response(
        200,
        json={
            "job_id": "job-123",
            "status": "done",
            "job_name": "create_payment",
            "attempt": 1,
            "max_tries": 3,
            "request_id": "req-1",
            "created_at": "2026-05-26T18:00:00Z",
            "started_at": "2026-05-26T18:00:01Z",
            "finished_at": "2026-05-26T18:00:02Z",
            "error_code": None,
            "error_message": None,
        },
    )

    with patch("httpx.AsyncClient.request", return_value=mock_response) as mock_request:
        res = await client.get_job("job-123")
        assert res["job_id"] == "job-123"
        assert res["status"] == "done"
        mock_request.assert_called_once()
        assert mock_request.call_args[0][1].endswith("/v1/jobs/job-123")


@pytest.mark.asyncio
async def test_get_payment_success():
    client = BillingCoreClient()
    mock_response = httpx.Response(200, json={"payment_id": "pay-123", "status": "paid"})

    with patch("httpx.AsyncClient.request", return_value=mock_response) as mock_request:
        res = await client.get_payment("pay-123")
        assert res["payment_id"] == "pay-123"
        assert res["status"] == "paid"
        mock_request.assert_called_once()
        assert mock_request.call_args[0][1].endswith("/v1/payments/pay-123")


@pytest.mark.asyncio
async def test_retry_on_429_respects_retry_after():
    client = BillingCoreClient()
    # Primeira tentativa: 429 com Retry-After: 1
    # Segunda tentativa: 200 OK
    responses = [
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.Response(200, json={"status": "ok"}),
    ]

    with patch("httpx.AsyncClient.request", side_effect=responses) as mock_request, \
         patch("asyncio.sleep") as mock_sleep:
        res = await client.get_payment("pay-123")
        assert res["status"] == "ok"
        assert mock_request.call_count == 2
        mock_sleep.assert_called_once_with(1.0)


@pytest.mark.asyncio
async def test_retry_on_503_504_backoff():
    client = BillingCoreClient()
    # Primeira e segunda tentativas: 503
    # Terceira tentativa: 200 OK
    responses = [
        httpx.Response(503),
        httpx.Response(504),
        httpx.Response(200, json={"status": "ok"}),
    ]

    with patch("httpx.AsyncClient.request", side_effect=responses) as mock_request, \
         patch("asyncio.sleep") as mock_sleep:
        res = await client.get_payment("pay-123")
        assert res["status"] == "ok"
        assert mock_request.call_count == 3
        # Backoff exponencial: 2 ** 0 = 1s, depois 2 ** 1 = 2s
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(1.0)
        mock_sleep.assert_any_call(2.0)


@pytest.mark.asyncio
async def test_auth_error_no_retry():
    client = BillingCoreClient()
    mock_response = httpx.Response(401)

    with patch("httpx.AsyncClient.request", return_value=mock_response) as mock_request:
        with pytest.raises(BillingCoreAuthError):
            await client.get_payment("pay-123")
        assert mock_request.call_count == 1  # Sem retry em 401


@pytest.mark.asyncio
async def test_conflict_error_no_retry():
    client = BillingCoreClient()
    mock_response = httpx.Response(409)

    with patch("httpx.AsyncClient.request", return_value=mock_response) as mock_request:
        with pytest.raises(BillingCoreError):
            await client.get_payment("pay-123")
        assert mock_request.call_count == 1  # Sem retry em 409


@pytest.mark.asyncio
async def test_timeout_error_retry_and_exception():
    client = BillingCoreClient()

    with patch("httpx.AsyncClient.request", side_effect=httpx.TimeoutException("timeout")) as mock_request, \
         patch("asyncio.sleep") as mock_sleep:
        with pytest.raises(BillingCoreUnavailableError):
            await client.get_payment("pay-123")
        assert mock_request.call_count == 3  # Tenta 3 vezes antes de levantar erro
        assert mock_sleep.call_count == 2
