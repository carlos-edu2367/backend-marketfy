"""
PR3 — Testes do FakeFiscalProvider e contrato FiscalProviderGateway.

Cobre:
- FakeFiscalProvider: todos os cenários (authorize, reject, timeout, provider_error,
  duplicate_ref, sefaz_unavailable)
- Consistência consult/cancel após authorize
- build_provider_ref — formato e determinismo
- Cenário de download XML/PDF
- FakeFiscalProvider nunca em produção (provider_factory)
"""
import asyncio
import os
import sys
import uuid

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from infra.providers.fiscal.base import EmitResultStatus, build_provider_ref
from infra.providers.fiscal.fake_provider import FakeFiscalProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ref() -> str:
    return build_provider_ref(uuid.uuid4(), uuid.uuid4())


def _run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("_run só pode ser usado por testes síncronos")


TOKEN = "fake-token"
ENV = "homologacao"
PAYLOAD = {
    "ref": "test",
    "natureza_operacao": "VENDA",
    "items": [{"codigo_ncm": "22021000"}],
}


# ---------------------------------------------------------------------------
# build_provider_ref
# ---------------------------------------------------------------------------

def test_build_provider_ref_format():
    market_id = uuid.uuid4()
    sale_id = uuid.uuid4()
    ref = build_provider_ref(market_id, sale_id)
    assert ref.startswith("marketfy-")
    assert str(market_id) in ref
    assert str(sale_id) in ref


def test_build_provider_ref_is_deterministic():
    market_id = uuid.uuid4()
    sale_id = uuid.uuid4()
    assert build_provider_ref(market_id, sale_id) == build_provider_ref(market_id, sale_id)


def test_build_provider_ref_different_for_different_sales():
    market_id = uuid.uuid4()
    ref1 = build_provider_ref(market_id, uuid.uuid4())
    ref2 = build_provider_ref(market_id, uuid.uuid4())
    assert ref1 != ref2


# ---------------------------------------------------------------------------
# Cenário AUTHORIZE
# ---------------------------------------------------------------------------

def test_fake_authorize_returns_authorized_status():
    provider = FakeFiscalProvider(scenario=FakeFiscalProvider.SCENARIO_AUTHORIZE)
    ref = _ref()
    result = _run(provider.emit_nfce(ref, PAYLOAD, TOKEN, ENV))
    assert result.status == EmitResultStatus.AUTHORIZED
    assert result.access_key is not None
    assert result.protocol is not None
    assert result.sefaz_status_code == "100"
    assert result.http_status == 200


def test_fake_authorize_is_not_recoverable():
    provider = FakeFiscalProvider(scenario=FakeFiscalProvider.SCENARIO_AUTHORIZE)
    result = _run(provider.emit_nfce(_ref(), PAYLOAD, TOKEN, ENV))
    assert result.is_recoverable is False


# ---------------------------------------------------------------------------
# Cenário REJECT
# ---------------------------------------------------------------------------

def test_fake_reject_returns_rejected_status():
    provider = FakeFiscalProvider(
        scenario=FakeFiscalProvider.SCENARIO_REJECT,
        reject_code="225",
        reject_message="NCM inválido",
    )
    result = _run(provider.emit_nfce(_ref(), PAYLOAD, TOKEN, ENV))
    assert result.status == EmitResultStatus.REJECTED
    assert result.sefaz_status_code == "225"
    assert "NCM" in result.sefaz_message
    assert result.is_recoverable is False


# ---------------------------------------------------------------------------
# Cenário TIMEOUT
# ---------------------------------------------------------------------------

def test_fake_timeout_raises_asyncio_timeout():
    provider = FakeFiscalProvider(scenario=FakeFiscalProvider.SCENARIO_TIMEOUT)
    with pytest.raises(asyncio.TimeoutError):
        _run(provider.emit_nfce(_ref(), PAYLOAD, TOKEN, ENV))


# ---------------------------------------------------------------------------
# Cenário PROVIDER_ERROR
# ---------------------------------------------------------------------------

def test_fake_provider_error_returns_recoverable_error():
    provider = FakeFiscalProvider(scenario=FakeFiscalProvider.SCENARIO_PROVIDER_ERROR)
    result = _run(provider.emit_nfce(_ref(), PAYLOAD, TOKEN, ENV))
    assert result.status == EmitResultStatus.PROVIDER_ERROR
    assert result.is_recoverable is True
    assert result.http_status == 503


# ---------------------------------------------------------------------------
# Cenário DUPLICATE_REF
# ---------------------------------------------------------------------------

def test_fake_duplicate_ref_returns_non_recoverable():
    provider = FakeFiscalProvider(scenario=FakeFiscalProvider.SCENARIO_DUPLICATE_REF)
    result = _run(provider.emit_nfce(_ref(), PAYLOAD, TOKEN, ENV))
    assert result.status == EmitResultStatus.DUPLICATE_REF
    assert result.is_recoverable is False


# ---------------------------------------------------------------------------
# Cenário SEFAZ_UNAVAILABLE
# ---------------------------------------------------------------------------

def test_fake_sefaz_unavailable_is_recoverable():
    provider = FakeFiscalProvider(scenario=FakeFiscalProvider.SCENARIO_SEFAZ_UNAVAILABLE)
    result = _run(provider.emit_nfce(_ref(), PAYLOAD, TOKEN, ENV))
    assert result.status == EmitResultStatus.SEFAZ_UNAVAILABLE
    assert result.is_recoverable is True


# ---------------------------------------------------------------------------
# Consult — consistência com emit
# ---------------------------------------------------------------------------

def test_consult_after_authorize_returns_found():
    provider = FakeFiscalProvider(scenario=FakeFiscalProvider.SCENARIO_AUTHORIZE)
    ref = _ref()
    _run(provider.emit_nfce(ref, PAYLOAD, TOKEN, ENV))
    consult = _run(provider.consult_nfce(ref, TOKEN, ENV))
    assert consult.found is True
    assert consult.status == EmitResultStatus.AUTHORIZED
    assert consult.access_key is not None


def test_consult_without_prior_emit_returns_not_found():
    provider = FakeFiscalProvider(scenario=FakeFiscalProvider.SCENARIO_AUTHORIZE)
    consult = _run(provider.consult_nfce("marketfy-unknown-ref", TOKEN, ENV))
    assert consult.found is False


# ---------------------------------------------------------------------------
# Cancel — consistência com emit
# ---------------------------------------------------------------------------

def test_cancel_authorized_doc_succeeds():
    provider = FakeFiscalProvider(scenario=FakeFiscalProvider.SCENARIO_AUTHORIZE)
    ref = _ref()
    _run(provider.emit_nfce(ref, PAYLOAD, TOKEN, ENV))
    cancel = _run(provider.cancel_nfce(ref, "ACCESS_KEY", "Cancelamento de teste com texto", TOKEN, ENV))
    assert cancel.success is True
    assert cancel.protocol is not None


def test_cancel_unknown_ref_fails():
    provider = FakeFiscalProvider(scenario=FakeFiscalProvider.SCENARIO_AUTHORIZE)
    cancel = _run(provider.cancel_nfce("marketfy-unknown-ref", "ACCESS_KEY", "Teste", TOKEN, ENV))
    assert cancel.success is False


def test_cancel_clears_from_authorized_set():
    provider = FakeFiscalProvider(scenario=FakeFiscalProvider.SCENARIO_AUTHORIZE)
    ref = _ref()
    _run(provider.emit_nfce(ref, PAYLOAD, TOKEN, ENV))
    _run(provider.cancel_nfce(ref, "ACCESS_KEY", "Cancelamento", TOKEN, ENV))
    # Após cancelar, consulta não deve encontrar
    consult = _run(provider.consult_nfce(ref, TOKEN, ENV))
    assert consult.found is False


# ---------------------------------------------------------------------------
# Download XML / PDF
# ---------------------------------------------------------------------------

def test_download_xml_after_authorize_returns_bytes():
    provider = FakeFiscalProvider(scenario=FakeFiscalProvider.SCENARIO_AUTHORIZE)
    ref = _ref()
    _run(provider.emit_nfce(ref, PAYLOAD, TOKEN, ENV))
    xml = _run(provider.download_xml(ref, TOKEN, ENV))
    assert xml is not None
    assert b"nfce" in xml


def test_download_xml_without_emit_returns_none():
    provider = FakeFiscalProvider(scenario=FakeFiscalProvider.SCENARIO_AUTHORIZE)
    xml = _run(provider.download_xml("marketfy-unknown", TOKEN, ENV))
    assert xml is None


def test_download_pdf_after_authorize_returns_bytes():
    provider = FakeFiscalProvider(scenario=FakeFiscalProvider.SCENARIO_AUTHORIZE)
    ref = _ref()
    _run(provider.emit_nfce(ref, PAYLOAD, TOKEN, ENV))
    pdf = _run(provider.download_pdf(ref, TOKEN, ENV))
    assert pdf is not None
    assert pdf.startswith(b"%PDF")


# ---------------------------------------------------------------------------
# register_company
# ---------------------------------------------------------------------------

def test_register_company_succeeds():
    provider = FakeFiscalProvider()
    result = _run(provider.register_company("12345678000195", {}, TOKEN, ENV))
    assert result.success is True


# ---------------------------------------------------------------------------
# Provider factory — fake bloqueado em produção
# ---------------------------------------------------------------------------

def test_factory_blocks_fake_in_production():
    from unittest.mock import patch, MagicMock
    from infra.providers.fiscal.provider_factory import get_fiscal_provider, reset_provider_singletons
    reset_provider_singletons()
    fake_settings = MagicMock()
    fake_settings.is_production = True
    with patch("infra.config.settings.get_settings", return_value=fake_settings):
        with pytest.raises(RuntimeError, match="[Ff]ake|[Pp]rodução|production"):
            get_fiscal_provider("fake", "producao")
    reset_provider_singletons()
