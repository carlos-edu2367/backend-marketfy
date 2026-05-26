"""
PR11 — Testes do FiscalInutilizacaoService.

Cobre:
- Validação: justificativa curta, número inicial inválido, range invertido, range > 1000
- Idempotência: intervalo já autorizado retorna existente sem chamar provider
- Idempotência: intervalo pendente reutiliza entidade existente
- Chave de idempotência: CNPJ formatado é normalizado corretamente
- Sucesso: provider retorna success → entidade mark_authorized + salva
- Falha de provider: result.success=False → entidade mark_failed + salva
- Exceção no provider: BusinessRuleException propagada + entidade mark_failed
- list_inutilizacoes: delega ao repo com parâmetros corretos
"""
import os
import sys
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.services.fiscal.fiscal_inutilizacao_service import FiscalInutilizacaoService
from domain.fiscal import FiscalEnvironment, FiscalNumberInutilizacao
from domain.shared import BusinessRuleException
from infra.providers.fiscal.base import ProviderInutilizacaoResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_inutilizacao(**kw) -> FiscalNumberInutilizacao:
    defaults = dict(
        market_id=uuid.uuid4(),
        provider="focus_nfe",
        environment=FiscalEnvironment.HOMOLOGATION,
        cnpj="12345678000195",
        document_type="nfce",
        series=1,
        start_number=10,
        end_number=20,
        justification="Justificativa de teste para inutilizacao",
        requested_by_id=uuid.uuid4(),
        status="pending",
        idempotency_key="12345678000195:1:10:20",
    )
    defaults.update(kw)
    return FiscalNumberInutilizacao(**defaults)


def _make_service(existing=None, provider_result=None, provider_raises=None):
    repo = AsyncMock()
    repo.get_by_idempotency_key.return_value = existing

    async def save_entity(entity):
        return entity
    repo.save.side_effect = save_entity

    repo.list_by_market.return_value = ([], 0)

    provider = AsyncMock()
    if provider_raises:
        provider.inutilize_numbering.side_effect = provider_raises
    else:
        provider.inutilize_numbering.return_value = provider_result or ProviderInutilizacaoResult(
            success=True, protocol="PROT-001", message="Inutilizado com sucesso"
        )

    return FiscalInutilizacaoService(inutilizacao_repo=repo, provider=provider), repo, provider


VALID_PARAMS = dict(
    market_id=uuid.uuid4(),
    requested_by_id=uuid.uuid4(),
    cnpj="12345678000195",
    document_type="nfce",
    series=1,
    start_number=10,
    end_number=20,
    justification="Justificativa longa o suficiente para SEFAZ",
    provider_name="focus_nfe",
    environment=FiscalEnvironment.HOMOLOGATION,
    api_token="token-abc",
)


# ---------------------------------------------------------------------------
# Validações de entrada
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_short_justification_raises():
    svc, _, _ = _make_service()
    with pytest.raises(BusinessRuleException, match="15 caracteres"):
        await svc.request_inutilizacao(**{**VALID_PARAMS, "justification": "Curta"})


@pytest.mark.asyncio
async def test_start_number_zero_raises():
    svc, _, _ = _make_service()
    with pytest.raises(BusinessRuleException, match="maior que zero"):
        await svc.request_inutilizacao(**{**VALID_PARAMS, "start_number": 0, "end_number": 5})


@pytest.mark.asyncio
async def test_end_before_start_raises():
    svc, _, _ = _make_service()
    with pytest.raises(BusinessRuleException, match=">="):
        await svc.request_inutilizacao(**{**VALID_PARAMS, "start_number": 10, "end_number": 5})


@pytest.mark.asyncio
async def test_range_over_1000_raises():
    svc, _, _ = _make_service()
    with pytest.raises(BusinessRuleException, match="1000"):
        await svc.request_inutilizacao(**{**VALID_PARAMS, "start_number": 1, "end_number": 1001})


@pytest.mark.asyncio
async def test_range_exactly_1000_is_valid():
    svc, _, _ = _make_service()
    result = await svc.request_inutilizacao(**{**VALID_PARAMS, "start_number": 1, "end_number": 1000})
    assert result.status == "authorized"


# ---------------------------------------------------------------------------
# Idempotência
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_existing_authorized_returns_without_calling_provider():
    existing = _make_inutilizacao(status="authorized", protocol="PROT-OLD")
    svc, repo, provider = _make_service(existing=existing)
    result = await svc.request_inutilizacao(**VALID_PARAMS)
    assert result.status == "authorized"
    assert result.protocol == "PROT-OLD"
    provider.inutilize_numbering.assert_not_called()


@pytest.mark.asyncio
async def test_existing_pending_reuses_entity():
    existing = _make_inutilizacao(status="pending")
    svc, repo, provider = _make_service(existing=existing)
    result = await svc.request_inutilizacao(**VALID_PARAMS)
    # save was NOT called for creation (entity reused), but called after provider result
    assert repo.save.call_count == 1
    assert result.status == "authorized"


@pytest.mark.asyncio
async def test_new_entity_is_saved_before_provider_call():
    svc, repo, provider = _make_service(existing=None)
    await svc.request_inutilizacao(**VALID_PARAMS)
    # save called twice: once to persist new entity, once after provider result
    assert repo.save.call_count == 2


# ---------------------------------------------------------------------------
# Chave de idempotência
# ---------------------------------------------------------------------------

def test_idempotency_key_strips_cnpj_formatting():
    key = FiscalInutilizacaoService._idempotency_key("12.345.678/0001-95", 1, 10, 20)
    assert key == "12345678000195:1:10:20"


def test_idempotency_key_already_clean():
    key = FiscalInutilizacaoService._idempotency_key("12345678000195", 2, 5, 100)
    assert key == "12345678000195:2:5:100"


# ---------------------------------------------------------------------------
# Sucesso do provider
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_successful_provider_marks_authorized():
    result_mock = ProviderInutilizacaoResult(
        success=True, protocol="PROT-999", message="OK"
    )
    svc, _, _ = _make_service(provider_result=result_mock)
    entity = await svc.request_inutilizacao(**VALID_PARAMS)
    assert entity.status == "authorized"
    assert entity.protocol == "PROT-999"
    assert entity.authorized_at is not None


# ---------------------------------------------------------------------------
# Falha do provider (result.success = False)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failed_provider_marks_failed():
    result_mock = ProviderInutilizacaoResult(
        success=False, error_message="Rejeição SEFAZ 539"
    )
    svc, _, _ = _make_service(provider_result=result_mock)
    entity = await svc.request_inutilizacao(**VALID_PARAMS)
    assert entity.status == "failed"
    assert "539" in (entity.error_message or "")


@pytest.mark.asyncio
async def test_failed_provider_without_message_uses_fallback():
    result_mock = ProviderInutilizacaoResult(success=False, error_message=None)
    svc, _, _ = _make_service(provider_result=result_mock)
    entity = await svc.request_inutilizacao(**VALID_PARAMS)
    assert entity.status == "failed"
    assert entity.error_message is not None


# ---------------------------------------------------------------------------
# Exceção no provider
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_provider_exception_raises_business_rule():
    svc, repo, _ = _make_service(provider_raises=Exception("Timeout"))
    with pytest.raises(BusinessRuleException, match="Timeout"):
        await svc.request_inutilizacao(**VALID_PARAMS)


@pytest.mark.asyncio
async def test_provider_exception_saves_failed_entity():
    svc, repo, _ = _make_service(provider_raises=Exception("Connection refused"))
    with pytest.raises(BusinessRuleException):
        await svc.request_inutilizacao(**VALID_PARAMS)
    # The last save call should have an entity with status=failed
    last_saved: FiscalNumberInutilizacao = repo.save.call_args[0][0]
    assert last_saved.status == "failed"


# ---------------------------------------------------------------------------
# list_inutilizacoes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_delegates_to_repo():
    svc, repo, _ = _make_service()
    market_id = uuid.uuid4()
    repo.list_by_market.return_value = ([_make_inutilizacao()], 1)
    items, total = await svc.list_inutilizacoes(market_id, status="authorized", limit=10, offset=5)
    repo.list_by_market.assert_called_once_with(
        market_id, status="authorized", limit=10, offset=5
    )
    assert total == 1


@pytest.mark.asyncio
async def test_list_no_status_filter():
    svc, repo, _ = _make_service()
    market_id = uuid.uuid4()
    repo.list_by_market.return_value = ([], 0)
    items, total = await svc.list_inutilizacoes(market_id)
    repo.list_by_market.assert_called_once_with(market_id, status=None, limit=50, offset=0)
    assert total == 0
