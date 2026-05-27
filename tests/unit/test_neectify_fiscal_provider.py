"""
PR1 — Testes do NeectifyFiscalProvider, client e gateway.

Cobertura mínima (20+ testes):
- create_issuer_success
- create_issuer_cnpj_duplicate → FiscalValidationError
- upload_certificate_multipart
- activate_certificate_success
- emit_nfce_returns_queued
- emit_nfce_idempotency_key_header
- emit_nfce_same_key_returns_same_doc
- get_nfce_status_authorized
- get_nfce_status_rejected
- cancel_nfce_success
- download_xml_returns_bytes
- inutilize_range_success
- retry_on_503
- no_retry_on_422
- auth_error_on_401
- timeout_raises_error
- onboarding_full_flow
- gateway_selects_neectify_provider
- fake_provider_still_works
- emission_service_payload_uses_neectify_format
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from domain.fiscal import FiscalAuthError, FiscalValidationError, NeectifyOnboardingStep
from infra.clients.neectify_fiscal_client import NeectifyFiscalClient
from infra.providers.fiscal.neectify_fiscal_provider import NeectifyFiscalProvider
from infra.providers.fiscal.base import EmitResultStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_response(status_code: int, json_data: dict = None, content: bytes = None) -> httpx.Response:
    """Cria um httpx.Response fake com status_code e body."""
    if content is not None:
        return httpx.Response(status_code, content=content)
    import json as _json
    body = _json.dumps(json_data or {}).encode()
    return httpx.Response(
        status_code,
        content=body,
        headers={"content-type": "application/json"},
    )


def _make_client(responses: list) -> NeectifyFiscalClient:
    """Cria um NeectifyFiscalClient cujo transporte retorna responses em sequência."""
    transport = httpx.MockTransport(lambda req: responses.pop(0))
    client_obj = NeectifyFiscalClient.__new__(NeectifyFiscalClient)
    client_obj._api_key = "test-key"
    client_obj._base_url = "https://api.neectify.com"
    client_obj._timeout = 30
    inner = httpx.AsyncClient(
        base_url="https://api.neectify.com",
        headers={"Authorization": "Bearer test-key"},
        transport=transport,
    )
    client_obj._client = inner
    return client_obj


def _make_provider(responses: list) -> NeectifyFiscalProvider:
    client = _make_client(responses)
    return NeectifyFiscalProvider(client=client)


ISSUER_ID = "iss_01HZXAMPLE000000000001"
CERT_ID = "crt_01HZXAMPLE000000000001"
CONFIG_ID = "cfg_01HZXAMPLE000000000001"
DOC_ID = "doc_01HZXAMPLE000000000001"


# ---------------------------------------------------------------------------
# test_create_issuer_success
# ---------------------------------------------------------------------------

def test_create_issuer_success():
    payload = {"legal_name": "Supermercado do Joao Ltda", "cnpj": "12345678000195"}
    resp_data = {
        "id": ISSUER_ID,
        "legal_name": "Supermercado do Joao Ltda",
        "cnpj": "12345678000195",
        "status": "active",
    }
    provider = _make_provider([_make_response(201, resp_data)])
    result = _run(provider.create_issuer(payload))
    assert result["id"] == ISSUER_ID
    assert result["status"] == "active"


# ---------------------------------------------------------------------------
# test_create_issuer_cnpj_duplicate → FiscalValidationError
# ---------------------------------------------------------------------------

def test_create_issuer_cnpj_duplicate():
    payload = {"cnpj": "12345678000195"}
    resp_data = {"detail": "CNPJ already registered"}
    provider = _make_provider([_make_response(422, resp_data)])
    with pytest.raises(FiscalValidationError):
        _run(provider.create_issuer(payload))


# ---------------------------------------------------------------------------
# test_upload_certificate_multipart
# ---------------------------------------------------------------------------

def test_upload_certificate_multipart():
    pfx_bytes = b"fake-pfx-content"
    resp_data = {"id": CERT_ID, "status": "pending_validation"}

    captured_requests = []

    def mock_transport(req: httpx.Request) -> httpx.Response:
        captured_requests.append(req)
        import json as _json
        return httpx.Response(
            201,
            content=_json.dumps(resp_data).encode(),
            headers={"content-type": "application/json"},
        )

    transport = httpx.MockTransport(mock_transport)
    client_obj = NeectifyFiscalClient.__new__(NeectifyFiscalClient)
    client_obj._api_key = "test-key"
    client_obj._base_url = "https://api.neectify.com"
    client_obj._timeout = 30
    client_obj._client = httpx.AsyncClient(
        base_url="https://api.neectify.com",
        headers={"Authorization": "Bearer test-key"},
        transport=transport,
    )
    provider = NeectifyFiscalProvider(client=client_obj)

    result = _run(provider.upload_certificate(
        issuer_id=ISSUER_ID,
        pfx_bytes=pfx_bytes,
        password="senha123",
        environment="homologation",
    ))

    assert result["id"] == CERT_ID
    # Verificar que a requisição foi multipart e para o endpoint correto
    req = captured_requests[0]
    content_type = req.headers.get("content-type", "")
    assert "multipart" in content_type
    assert req.url.path == f"/v1/issuers/{ISSUER_ID}/certificates"


# ---------------------------------------------------------------------------
# test_activate_certificate_success
# ---------------------------------------------------------------------------

def test_activate_certificate_success():
    resp_data = {"id": CERT_ID, "status": "active"}
    provider = _make_provider([_make_response(200, resp_data)])
    result = _run(provider.activate_certificate(CERT_ID))
    assert result["id"] == CERT_ID


# ---------------------------------------------------------------------------
# test_emit_nfce_returns_queued
# ---------------------------------------------------------------------------

def test_emit_nfce_returns_queued():
    resp_data = {
        "id": DOC_ID,
        "status": "queued",
        "environment": "homologation",
        "issuer_id": ISSUER_ID,
        "external_id": "sale_1",
    }
    provider = _make_provider([_make_response(202, resp_data)])
    result = _run(provider.emit_nfce(
        provider_ref="marketfy:market1:sale:sale1:nfce",
        payload={"issuer_id": ISSUER_ID},
        api_token="",
        environment="homologation",
    ))
    assert result.provider_status == "queued"
    assert result.provider_ref == DOC_ID


# ---------------------------------------------------------------------------
# test_emit_nfce_idempotency_key_header
# ---------------------------------------------------------------------------

def test_emit_nfce_idempotency_key_header():
    captured = []

    def mock_transport(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        import json as _json
        data = {"id": DOC_ID, "status": "queued"}
        return httpx.Response(202, content=_json.dumps(data).encode(),
                              headers={"content-type": "application/json"})

    transport = httpx.MockTransport(mock_transport)
    client_obj = NeectifyFiscalClient.__new__(NeectifyFiscalClient)
    client_obj._api_key = "test-key"
    client_obj._base_url = "https://api.neectify.com"
    client_obj._timeout = 30
    client_obj._client = httpx.AsyncClient(
        base_url="https://api.neectify.com",
        headers={"Authorization": "Bearer test-key"},
        transport=transport,
    )
    provider = NeectifyFiscalProvider(client=client_obj)

    idem_key = "marketfy:market1:sale:sale1:nfce"
    _run(provider.emit_nfce(
        provider_ref=idem_key,
        payload={"issuer_id": ISSUER_ID},
        api_token="",
        environment="homologation",
    ))

    req = captured[0]
    assert req.headers.get("idempotency-key") == idem_key


# ---------------------------------------------------------------------------
# test_emit_nfce_same_key_returns_same_doc
# ---------------------------------------------------------------------------

def test_emit_nfce_same_key_returns_same_doc():
    resp_data = {"id": DOC_ID, "status": "queued"}
    provider = _make_provider([
        _make_response(202, resp_data),
        _make_response(202, resp_data),
    ])
    idem_key = "marketfy:market1:sale:sale1:nfce"
    r1 = _run(provider.emit_nfce(provider_ref=idem_key, payload={}, api_token="", environment="homologation"))
    r2 = _run(provider.emit_nfce(provider_ref=idem_key, payload={}, api_token="", environment="homologation"))
    assert r1.provider_ref == r2.provider_ref


# ---------------------------------------------------------------------------
# test_get_nfce_status_authorized
# ---------------------------------------------------------------------------

def test_get_nfce_status_authorized():
    resp_data = {
        "id": DOC_ID,
        "status": "authorized",
        "access_key": "52260512345678000195650010000001001567439108",
        "series": 1,
        "number": 100,
    }
    provider = _make_provider([_make_response(200, resp_data)])
    consult = _run(provider.consult_nfce(
        provider_ref=DOC_ID, api_token="", environment="homologation"
    ))
    assert consult.found is True
    assert consult.status == EmitResultStatus.AUTHORIZED
    assert consult.access_key == "52260512345678000195650010000001001567439108"
    assert consult.number == 100


# ---------------------------------------------------------------------------
# test_get_nfce_status_rejected
# ---------------------------------------------------------------------------

def test_get_nfce_status_rejected():
    resp_data = {"id": DOC_ID, "status": "rejected", "sefaz_message": "CNPJ inválido"}
    provider = _make_provider([_make_response(200, resp_data)])
    consult = _run(provider.consult_nfce(
        provider_ref=DOC_ID, api_token="", environment="homologation"
    ))
    assert consult.status == EmitResultStatus.REJECTED
    assert consult.sefaz_message == "CNPJ inválido"


# ---------------------------------------------------------------------------
# test_cancel_nfce_success
# ---------------------------------------------------------------------------

def test_cancel_nfce_success():
    resp_data = {
        "id": DOC_ID,
        "status": "cancel_requested",
        "access_key": "52260512345678000195650010000001001567439108",
    }
    provider = _make_provider([_make_response(202, resp_data)])
    result = _run(provider.cancel_nfce(
        provider_ref=DOC_ID,
        access_key="52260512345678000195650010000001001567439108",
        justification="Cancelamento solicitado pelo cliente",
        api_token="",
        environment="homologation",
    ))
    assert result.success is True


# ---------------------------------------------------------------------------
# test_download_xml_returns_bytes
# ---------------------------------------------------------------------------

def test_download_xml_returns_bytes():
    xml_bytes = b'<?xml version="1.0"?><nfceProc><NFe/></nfceProc>'
    provider = _make_provider([_make_response(200, content=xml_bytes)])
    result = _run(provider.download_xml(
        provider_ref=DOC_ID, api_token="", environment="homologation"
    ))
    assert result == xml_bytes


# ---------------------------------------------------------------------------
# test_inutilize_range_success
# ---------------------------------------------------------------------------

def test_inutilize_range_success():
    resp_data = {
        "id": "inut_01HZXAMPLE000000000001",
        "status": "queued",
        "series": 1,
        "number_start": 101,
        "number_end": 105,
    }
    provider = _make_provider([_make_response(202, resp_data)])
    result = _run(provider.inutilize_numbering(
        cnpj="12345678000195",
        series=1,
        start_number=101,
        end_number=105,
        justification="Pulo acidental no sequencial do PDV",
        api_token="",
        environment="homologation",
    ))
    assert result.success is True


# ---------------------------------------------------------------------------
# test_retry_on_503
# ---------------------------------------------------------------------------

def test_retry_on_503():
    call_count = [0]

    def mock_transport(req: httpx.Request) -> httpx.Response:
        call_count[0] += 1
        if call_count[0] < 3:
            return httpx.Response(503, content=b"Service Unavailable")
        import json as _json
        data = {"id": DOC_ID, "status": "queued"}
        return httpx.Response(202, content=_json.dumps(data).encode(),
                              headers={"content-type": "application/json"})

    transport = httpx.MockTransport(mock_transport)
    client_obj = NeectifyFiscalClient.__new__(NeectifyFiscalClient)
    client_obj._api_key = "test-key"
    client_obj._base_url = "https://api.neectify.com"
    client_obj._timeout = 30

    # Substituir sleep por no-op para acelerar o teste
    async def _no_sleep(seconds):
        pass

    client_obj._client = httpx.AsyncClient(
        base_url="https://api.neectify.com",
        headers={"Authorization": "Bearer test-key"},
        transport=transport,
    )
    provider = NeectifyFiscalProvider(client=client_obj)

    import infra.clients.neectify_fiscal_client as client_module
    original_sleep = asyncio.sleep

    async def _fast_sleep(n):
        pass

    loop = asyncio.get_event_loop()

    async def run():
        with patch("asyncio.sleep", _fast_sleep):
            return await provider.emit_nfce(
                provider_ref="key1",
                payload={"issuer_id": ISSUER_ID},
                api_token="",
                environment="homologation",
            )

    result = loop.run_until_complete(run())
    assert call_count[0] == 3
    assert result.provider_status == "queued"


# ---------------------------------------------------------------------------
# test_no_retry_on_422
# ---------------------------------------------------------------------------

def test_no_retry_on_422():
    call_count = [0]

    def mock_transport(req: httpx.Request) -> httpx.Response:
        call_count[0] += 1
        return httpx.Response(422, content=b'{"detail":"Invalid payload"}',
                              headers={"content-type": "application/json"})

    transport = httpx.MockTransport(mock_transport)
    client_obj = NeectifyFiscalClient.__new__(NeectifyFiscalClient)
    client_obj._api_key = "test-key"
    client_obj._base_url = "https://api.neectify.com"
    client_obj._timeout = 30
    client_obj._client = httpx.AsyncClient(
        base_url="https://api.neectify.com",
        headers={"Authorization": "Bearer test-key"},
        transport=transport,
    )
    provider = NeectifyFiscalProvider(client=client_obj)

    result = _run(provider.emit_nfce(
        provider_ref="key1",
        payload={"issuer_id": ISSUER_ID},
        api_token="",
        environment="homologation",
    ))

    # 422 não retenta — apenas 1 tentativa, resultado é REJECTED
    assert call_count[0] == 1
    assert result.status == EmitResultStatus.REJECTED


# ---------------------------------------------------------------------------
# test_auth_error_on_401
# ---------------------------------------------------------------------------

def test_auth_error_on_401():
    def mock_transport(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, content=b'{"detail":"Unauthorized"}',
                              headers={"content-type": "application/json"})

    transport = httpx.MockTransport(mock_transport)
    client_obj = NeectifyFiscalClient.__new__(NeectifyFiscalClient)
    client_obj._api_key = "bad-key"
    client_obj._base_url = "https://api.neectify.com"
    client_obj._timeout = 30
    client_obj._client = httpx.AsyncClient(
        base_url="https://api.neectify.com",
        headers={"Authorization": "Bearer bad-key"},
        transport=transport,
    )
    provider = NeectifyFiscalProvider(client=client_obj)

    result = _run(provider.emit_nfce(
        provider_ref="key1",
        payload={},
        api_token="",
        environment="homologation",
    ))
    assert result.http_status == 401
    assert result.is_recoverable is False


# ---------------------------------------------------------------------------
# test_timeout_raises_error
# ---------------------------------------------------------------------------

def test_timeout_raises_error():
    def mock_transport(req: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timeout", request=req)

    transport = httpx.MockTransport(mock_transport)
    client_obj = NeectifyFiscalClient.__new__(NeectifyFiscalClient)
    client_obj._api_key = "test-key"
    client_obj._base_url = "https://api.neectify.com"
    client_obj._timeout = 1
    client_obj._client = httpx.AsyncClient(
        base_url="https://api.neectify.com",
        headers={"Authorization": "Bearer test-key"},
        transport=transport,
    )

    async def run():
        with patch("asyncio.sleep", AsyncMock()):
            with pytest.raises(httpx.TimeoutException):
                await client_obj.request("GET", "/v1/issuers/1")

    asyncio.get_event_loop().run_until_complete(run())


# ---------------------------------------------------------------------------
# test_onboarding_full_flow (issuer → config → cert → activate)
# ---------------------------------------------------------------------------

def test_onboarding_full_flow():
    responses = [
        _make_response(201, {"id": ISSUER_ID, "status": "active"}),           # create_issuer
        _make_response(201, {"id": CONFIG_ID, "issuer_id": ISSUER_ID}),        # create_nfce_config
        _make_response(201, {"id": CERT_ID, "status": "pending_validation"}),  # upload_certificate
        _make_response(200, {"id": CERT_ID, "status": "active"}),              # activate_certificate
    ]
    provider = _make_provider(responses)

    issuer = _run(provider.create_issuer({"cnpj": "12345678000195"}))
    assert issuer["id"] == ISSUER_ID

    config = _run(provider.create_nfce_config(ISSUER_ID, {"series": 1, "next_number": 1}))
    assert config["id"] == CONFIG_ID

    cert = _run(provider.upload_certificate(ISSUER_ID, b"pfx", "pass", "homologation"))
    assert cert["id"] == CERT_ID

    activated = _run(provider.activate_certificate(CERT_ID))
    assert activated["status"] == "active"


# ---------------------------------------------------------------------------
# test_gateway_selects_neectify_provider
# ---------------------------------------------------------------------------

def test_gateway_selects_neectify_provider():
    from infra.providers.fiscal.provider_factory import get_fiscal_provider, reset_provider_singletons
    from infra.providers.fiscal.neectify_fiscal_provider import NeectifyFiscalProvider

    reset_provider_singletons()

    with patch("infra.config.settings.get_settings") as mock_settings:
        s = MagicMock()
        s.is_production = False
        s.NEECTIFY_API_KEY = "test-key"
        s.NEECTIFY_BASE_URL = "https://api.neectify.com"
        s.NEECTIFY_TIMEOUT_SECONDS = 30
        mock_settings.return_value = s

        provider = get_fiscal_provider("neectify_fiscal")
        assert isinstance(provider, NeectifyFiscalProvider)

    reset_provider_singletons()


# ---------------------------------------------------------------------------
# test_fake_provider_still_works
# ---------------------------------------------------------------------------

def test_fake_provider_still_works():
    from infra.providers.fiscal.provider_factory import get_fiscal_provider, reset_provider_singletons
    from infra.providers.fiscal.fake_provider import FakeFiscalProvider

    reset_provider_singletons()

    with patch("infra.config.settings.get_settings") as mock_settings:
        s = MagicMock()
        s.is_production = False
        mock_settings.return_value = s

        provider = get_fiscal_provider("fake")
        assert isinstance(provider, FakeFiscalProvider)

    reset_provider_singletons()


# ---------------------------------------------------------------------------
# test_emission_service_payload_uses_neectify_format
# ---------------------------------------------------------------------------

def test_emission_service_payload_uses_neectify_format():
    """Verifica que build_neectify_payload produz o formato correto para Neectify."""
    import sys
    import os

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../app"))
    from application.services.fiscal.fiscal_pre_validator import FiscalPreValidator

    validator = FiscalPreValidator()

    # Mock de sale com itens e pagamentos
    item = MagicMock()
    item.product_id = uuid.uuid4()
    item.product_name_snapshot = "Refrigerante Cola 350ml"
    item.ncm_snapshot = "22021000"
    item.unit_price = 5.50
    item.quantity = 2.0
    item.total = 11.00

    payment = MagicMock()
    payment.method = MagicMock()
    payment.method.value = "cartao_credito"
    payment.amount = 11.00

    sale = MagicMock()
    sale.id = uuid.uuid4()
    sale.customer_cpf = "42978931023"
    sale.items = [item]
    sale.payments = [payment]

    fiscal_config = MagicMock()
    fiscal_config.environment = MagicMock()
    fiscal_config.environment.value = "homologacao"
    fiscal_config.default_cfop = "5405"
    fiscal_config.default_ncm = "22021000"

    payload = validator.build_neectify_payload(
        sale=sale,
        fiscal_config=fiscal_config,
        issuer_id=ISSUER_ID,
    )

    assert payload["issuer_id"] == ISSUER_ID
    assert payload["environment"] == "homologation"
    assert payload["external_id"] == str(sale.id)
    assert len(payload["items"]) == 1
    assert payload["items"][0]["ncm"] == "22021000"
    assert payload["items"][0]["unit_value"] == 5.50
    assert len(payload["payments"]) == 1
    assert payload["payments"][0]["payment_method"] == "credit_card"
    assert payload["consumer"]["document"] == "42978931023"


# ---------------------------------------------------------------------------
# test_neectify_onboarding_step_enum_values
# ---------------------------------------------------------------------------

def test_neectify_onboarding_step_enum_values():
    assert NeectifyOnboardingStep.PENDING.value == "pending"
    assert NeectifyOnboardingStep.ISSUER_CREATED.value == "issuer_created"
    assert NeectifyOnboardingStep.CONFIG_SET.value == "config_set"
    assert NeectifyOnboardingStep.CERT_UPLOADED.value == "cert_uploaded"
    assert NeectifyOnboardingStep.CERT_ACTIVE.value == "cert_active"
    assert NeectifyOnboardingStep.READY.value == "ready"


# ---------------------------------------------------------------------------
# test_fiscal_auth_error_and_validation_error_are_domain_exceptions
# ---------------------------------------------------------------------------

def test_fiscal_auth_error_is_exception():
    exc = FiscalAuthError("credencial inválida")
    assert isinstance(exc, Exception)
    assert "credencial" in str(exc)


def test_fiscal_validation_error_carries_details():
    details = {"field": "cnpj", "msg": "already registered"}
    exc = FiscalValidationError("payload inválido", details=details)
    assert exc.details == details


# ---------------------------------------------------------------------------
# test_consult_nfce_pending_status_returns_unknown
# ---------------------------------------------------------------------------

def test_consult_nfce_pending_status_returns_unknown():
    resp_data = {"id": DOC_ID, "status": "queued"}
    provider = _make_provider([_make_response(200, resp_data)])
    consult = _run(provider.consult_nfce(DOC_ID, "", "homologation"))
    assert consult.status == EmitResultStatus.UNKNOWN
    assert consult.found is True
