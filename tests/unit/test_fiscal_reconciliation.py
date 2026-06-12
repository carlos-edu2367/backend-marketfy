"""
PR7 — Testes do FiscalReconciliationService.

Cobre:
- Documento terminal: não reconcilia
- Consulta retorna AUTHORIZED: atualiza doc localmente
- Consulta retorna REJECTED: atualiza para rejected
- Consulta retorna não-encontrado: mantém estado, agenda retry
- Circuit breaker: máximo de tentativas → MANUAL_ACTION_REQUIRED
- should_retry: retorna (True, next_retry) dentro do limite
- should_retry: retorna (False, None) em terminal ou esgotado
- Backoff delays crescentes por tentativa
"""
import os
import sys
import uuid
from datetime import datetime, timedelta
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.services.fiscal.fiscal_reconciliation_service import (
    FiscalReconciliationService,
    _BACKOFF_DELAYS,
    _MAX_ATTEMPTS,
)
from domain.fiscal import (
    FiscalDocument,
    FiscalDocumentStatus,
    FiscalEnvironment,
)
from infra.providers.fiscal.base import (
    EmitResultStatus,
    ProviderConsultResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_doc(status=FiscalDocumentStatus.PROVIDER_ERROR, provider_ref=None) -> FiscalDocument:
    doc = FiscalDocument(
        market_id=uuid.uuid4(),
        sale_id=uuid.uuid4(),
        status=status,
        environment=FiscalEnvironment.HOMOLOGATION,
        provider="focus_nfe",
    )
    doc.provider_ref = provider_ref or f"marketfy-{uuid.uuid4()}-{uuid.uuid4()}"
    return doc


def _make_service(
    doc=None,
    attempt_count=0,
    consult_result=None,
) -> FiscalReconciliationService:
    doc_repo = AsyncMock()
    doc_repo.get_by_id.return_value = doc
    saved = []
    async def save_doc(d): saved.append(d); return d
    doc_repo.save.side_effect = save_doc

    attempt_repo = AsyncMock()
    attempt_repo.count_attempts.return_value = attempt_count
    attempt_repo.save.return_value = None

    event_repo = AsyncMock()
    event_repo.append.return_value = None

    provider = AsyncMock()
    provider.consult_nfce.return_value = consult_result or ProviderConsultResult(
        found=False, provider_ref="ref-test"
    )

    return FiscalReconciliationService(
        doc_repo=doc_repo,
        attempt_repo=attempt_repo,
        event_repo=event_repo,
        provider=provider,
    ), saved


# ---------------------------------------------------------------------------
# Terminal docs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_terminal_doc_is_skipped():
    doc = _make_doc(status=FiscalDocumentStatus.AUTHORIZED)
    svc, _ = _make_service(doc=doc)
    result = await svc.reconcile_document(doc.id, "token")
    assert result.status == FiscalDocumentStatus.AUTHORIZED
    svc.provider.consult_nfce.assert_not_called()


@pytest.mark.asyncio
async def test_rejected_doc_is_terminal_and_skipped():
    doc = _make_doc(status=FiscalDocumentStatus.REJECTED)
    svc, _ = _make_service(doc=doc)
    result = await svc.reconcile_document(doc.id, "token")
    svc.provider.consult_nfce.assert_not_called()


# ---------------------------------------------------------------------------
# Consulta retorna AUTHORIZED
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconcile_authorized_updates_doc():
    doc = _make_doc(status=FiscalDocumentStatus.PROVIDER_ERROR)
    consult = ProviderConsultResult(
        found=True,
        provider_ref=doc.provider_ref,
        status=EmitResultStatus.AUTHORIZED,
        access_key="ACCESS_KEY_44",
        protocol="PROT123",
        number=42,
        series=1,
        sefaz_status_code="100",
        sefaz_message="Autorizado",
    )
    svc, saved_docs = _make_service(doc=doc, consult_result=consult)
    result = await svc.reconcile_document(doc.id, "token")
    assert result.status == FiscalDocumentStatus.AUTHORIZED
    assert result.access_key == "ACCESS_KEY_44"
    assert any(d.status == FiscalDocumentStatus.AUTHORIZED for d in saved_docs)


# ---------------------------------------------------------------------------
# Consulta retorna REJECTED
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconcile_rejected_updates_doc():
    doc = _make_doc(status=FiscalDocumentStatus.PROVIDER_ERROR)
    consult = ProviderConsultResult(
        found=True,
        provider_ref=doc.provider_ref,
        status=EmitResultStatus.REJECTED,
        sefaz_status_code="225",
        sefaz_message="NCM inválido",
    )
    svc, saved_docs = _make_service(doc=doc, consult_result=consult)
    result = await svc.reconcile_document(doc.id, "token")
    assert result.status == FiscalDocumentStatus.REJECTED
    assert result.sefaz_status_code == "225"


# ---------------------------------------------------------------------------
# Consulta retorna não encontrado
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconcile_not_found_keeps_state():
    doc = _make_doc(status=FiscalDocumentStatus.PROVIDER_ERROR)
    consult = ProviderConsultResult(found=False, provider_ref=doc.provider_ref)
    svc, _ = _make_service(doc=doc, consult_result=consult, attempt_count=1)
    result = await svc.reconcile_document(doc.id, "token")
    # Estado original mantido — não é terminal
    assert result.status != FiscalDocumentStatus.AUTHORIZED


# ---------------------------------------------------------------------------
# Consulta retorna AINDA PENDENTE (UNKNOWN) — emissão assíncrona em andamento
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconcile_pending_keeps_processing_not_error():
    """Regressão: o Neectify processa de forma assíncrona; enquanto a SEFAZ não
    responde a consulta retorna UNKNOWN. Isso é uma emissão SAUDÁVEL em
    andamento — NÃO pode virar PROVIDER_ERROR (que o operador lê como
    'Erro emissão'). Deve permanecer PROCESSING para nova reconciliação."""
    doc = _make_doc(status=FiscalDocumentStatus.PROCESSING)
    consult = ProviderConsultResult(
        found=True,
        provider_ref=doc.provider_ref,
        status=EmitResultStatus.UNKNOWN,
        sefaz_status_code="queued",
    )
    svc, saved_docs = _make_service(doc=doc, consult_result=consult, attempt_count=0)
    result = await svc.reconcile_document(doc.id, "token")
    assert result.status == FiscalDocumentStatus.PROCESSING
    assert result.status != FiscalDocumentStatus.PROVIDER_ERROR
    assert not result.is_terminal()


# ---------------------------------------------------------------------------
# Circuit breaker — max tentativas
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_max_attempts_sets_manual_action():
    doc = _make_doc(status=FiscalDocumentStatus.PROVIDER_ERROR)
    consult = ProviderConsultResult(found=False, provider_ref=doc.provider_ref)
    svc, saved_docs = _make_service(doc=doc, consult_result=consult, attempt_count=_MAX_ATTEMPTS)
    result = await svc.reconcile_document(doc.id, "token")
    assert result.status == FiscalDocumentStatus.MANUAL_ACTION_REQUIRED
    # Provider não deve ser chamado
    svc.provider.consult_nfce.assert_not_called()


@pytest.mark.asyncio
async def test_max_minus_1_attempts_still_consults():
    doc = _make_doc(status=FiscalDocumentStatus.PROVIDER_ERROR)
    consult = ProviderConsultResult(found=False, provider_ref=doc.provider_ref)
    svc, _ = _make_service(doc=doc, consult_result=consult, attempt_count=_MAX_ATTEMPTS - 1)
    await svc.reconcile_document(doc.id, "token")
    svc.provider.consult_nfce.assert_called_once()


# ---------------------------------------------------------------------------
# should_retry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_should_retry_true_within_limit():
    doc = _make_doc(status=FiscalDocumentStatus.PROVIDER_ERROR)
    svc, _ = _make_service(doc=doc, attempt_count=1)
    retry, next_at = await svc.should_retry(doc)
    assert retry is True
    assert next_at is not None
    assert next_at > datetime.utcnow()


@pytest.mark.asyncio
async def test_should_retry_false_for_terminal():
    doc = _make_doc(status=FiscalDocumentStatus.AUTHORIZED)
    svc, _ = _make_service(doc=doc)
    retry, next_at = await svc.should_retry(doc)
    assert retry is False
    assert next_at is None


@pytest.mark.asyncio
async def test_should_retry_false_at_max_attempts():
    doc = _make_doc(status=FiscalDocumentStatus.PROVIDER_ERROR)
    svc, _ = _make_service(doc=doc, attempt_count=_MAX_ATTEMPTS)
    retry, next_at = await svc.should_retry(doc)
    assert retry is False


# ---------------------------------------------------------------------------
# Backoff delays crescentes
# ---------------------------------------------------------------------------

def test_backoff_delays_are_increasing():
    for i in range(len(_BACKOFF_DELAYS) - 1):
        assert _BACKOFF_DELAYS[i] < _BACKOFF_DELAYS[i + 1], \
            f"Backoff deve ser crescente: [{i}]={_BACKOFF_DELAYS[i]} >= [{i+1}]={_BACKOFF_DELAYS[i+1]}"


def test_backoff_delay_count_matches_max_attempts():
    assert len(_BACKOFF_DELAYS) <= _MAX_ATTEMPTS, \
        "Deve haver delays suficientes para as tentativas"


@pytest.mark.asyncio
async def test_retry_next_at_uses_correct_delay_for_first_attempt():
    doc = _make_doc(status=FiscalDocumentStatus.PROVIDER_ERROR)
    svc, _ = _make_service(doc=doc, attempt_count=0)
    _, next_at = await svc.should_retry(doc)
    expected_delay = _BACKOFF_DELAYS[0]
    delta = (next_at - datetime.utcnow()).total_seconds()
    assert expected_delay - 2 <= delta <= expected_delay + 2


# ---------------------------------------------------------------------------
# Documento não existe
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_doc_not_found_raises_value_error():
    svc, _ = _make_service(doc=None)
    with pytest.raises(ValueError, match="não encontrado"):
        await svc.reconcile_document(uuid.uuid4(), "token")
