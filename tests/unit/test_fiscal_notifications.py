"""
PR10 — Testes do FiscalNotificationService.

Cobre:
- Alertas de cota: 80%, 90%, 100%
- Abaixo de 80% não envia notificação
- Alertas de certificado: 30, 15, 7, 1 dia
- Sem alerta acima de 30 dias
- Rejeição de NFC-e
- Dedupe key correta por cenário
- get_unread e mark_read delegam ao repositório
"""
import os
import sys
import uuid
from datetime import datetime, timedelta
from typing import List, Optional
from unittest.mock import AsyncMock, call

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.services.fiscal.fiscal_notification_service import FiscalNotificationService
from domain.fiscal import (
    FiscalNotification,
    FiscalTenantConfig,
    NotificationSeverity,
    NotificationType,
    TaxRegime,
    ValidationStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_repo():
    repo = AsyncMock()
    repo.save.return_value = None
    repo.list_unread.return_value = []
    repo.mark_read.return_value = None
    return repo


def _svc(repo=None):
    return FiscalNotificationService(notification_repo=repo or _make_repo())


def _make_config(days_until_expiry: Optional[int] = 10, fingerprint: str = "abc123") -> FiscalTenantConfig:
    if days_until_expiry is not None:
        valid_until = datetime.utcnow() + timedelta(days=days_until_expiry)
    else:
        valid_until = None
    return FiscalTenantConfig(
        market_id=uuid.uuid4(),
        certificate_fingerprint=fingerprint,
        certificate_valid_until=valid_until,
        cnpj="12345678000195",
        legal_name="Empresa",
        certificate_storage_key="fiscal/cert",
        certificate_password_ciphertext="cipher",
        csc_id_ciphertext="cipher_cscid",
        csc_token_ciphertext="cipher_csctoken",
        tax_regime=TaxRegime.SIMPLES_NACIONAL,
        address_json={},
        nfce_series=1,
    )


# ---------------------------------------------------------------------------
# notify_quota_usage — limiares
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_quota_80_sends_warning():
    repo = _make_repo()
    svc = _svc(repo)
    owner_id = uuid.uuid4()
    market_id = uuid.uuid4()
    await svc.notify_quota_usage(owner_id, market_id, "202506", 80.0)
    repo.save.assert_called_once()
    notif: FiscalNotification = repo.save.call_args[0][0]
    assert notif.notification_type == NotificationType.FISCAL_LIMIT_80
    assert notif.severity == NotificationSeverity.WARNING


@pytest.mark.asyncio
async def test_quota_90_sends_warning():
    repo = _make_repo()
    svc = _svc(repo)
    await svc.notify_quota_usage(uuid.uuid4(), uuid.uuid4(), "202506", 90.0)
    repo.save.assert_called_once()
    notif: FiscalNotification = repo.save.call_args[0][0]
    assert notif.notification_type == NotificationType.FISCAL_LIMIT_90


@pytest.mark.asyncio
async def test_quota_100_sends_critical():
    repo = _make_repo()
    svc = _svc(repo)
    await svc.notify_quota_usage(uuid.uuid4(), uuid.uuid4(), "202506", 100.0)
    repo.save.assert_called_once()
    notif: FiscalNotification = repo.save.call_args[0][0]
    assert notif.notification_type == NotificationType.FISCAL_LIMIT_100
    assert notif.severity == NotificationSeverity.CRITICAL


@pytest.mark.asyncio
async def test_quota_below_80_sends_no_notification():
    repo = _make_repo()
    svc = _svc(repo)
    await svc.notify_quota_usage(uuid.uuid4(), uuid.uuid4(), "202506", 79.9)
    repo.save.assert_not_called()


@pytest.mark.asyncio
async def test_quota_50_sends_no_notification():
    repo = _make_repo()
    svc = _svc(repo)
    await svc.notify_quota_usage(uuid.uuid4(), uuid.uuid4(), "202506", 50.0)
    repo.save.assert_not_called()


# ---------------------------------------------------------------------------
# notify_quota_usage — dedupe_key correto
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_quota_80_dedupe_key_format():
    repo = _make_repo()
    svc = _svc(repo)
    owner_id = uuid.uuid4()
    await svc.notify_quota_usage(owner_id, uuid.uuid4(), "202506", 80.0)
    notif: FiscalNotification = repo.save.call_args[0][0]
    assert notif.dedupe_key == f"fiscal_limit_80:{owner_id}:202506"


@pytest.mark.asyncio
async def test_quota_100_dedupe_key_format():
    repo = _make_repo()
    svc = _svc(repo)
    owner_id = uuid.uuid4()
    await svc.notify_quota_usage(owner_id, uuid.uuid4(), "202506", 100.0)
    notif: FiscalNotification = repo.save.call_args[0][0]
    assert notif.dedupe_key == f"fiscal_limit_100:{owner_id}:202506"


@pytest.mark.asyncio
async def test_quota_90_takes_priority_over_80():
    """90% deve usar tipo FISCAL_LIMIT_90, não 80."""
    repo = _make_repo()
    svc = _svc(repo)
    await svc.notify_quota_usage(uuid.uuid4(), uuid.uuid4(), "202506", 92.5)
    notif: FiscalNotification = repo.save.call_args[0][0]
    assert notif.notification_type == NotificationType.FISCAL_LIMIT_90


# ---------------------------------------------------------------------------
# notify_certificate_expiring
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cert_expiring_30_days_sends_info():
    repo = _make_repo()
    svc = _svc(repo)
    cfg = _make_config(days_until_expiry=28)
    await svc.notify_certificate_expiring(uuid.uuid4(), uuid.uuid4(), cfg)
    repo.save.assert_called_once()
    notif: FiscalNotification = repo.save.call_args[0][0]
    assert notif.notification_type == NotificationType.CERTIFICATE_EXPIRING
    assert notif.severity == NotificationSeverity.INFO


@pytest.mark.asyncio
async def test_cert_expiring_15_days_sends_warning():
    repo = _make_repo()
    svc = _svc(repo)
    cfg = _make_config(days_until_expiry=14)
    await svc.notify_certificate_expiring(uuid.uuid4(), uuid.uuid4(), cfg)
    notif: FiscalNotification = repo.save.call_args[0][0]
    assert notif.severity == NotificationSeverity.WARNING


@pytest.mark.asyncio
async def test_cert_expiring_7_days_sends_critical():
    repo = _make_repo()
    svc = _svc(repo)
    cfg = _make_config(days_until_expiry=5)
    await svc.notify_certificate_expiring(uuid.uuid4(), uuid.uuid4(), cfg)
    notif: FiscalNotification = repo.save.call_args[0][0]
    assert notif.severity == NotificationSeverity.CRITICAL


@pytest.mark.asyncio
async def test_cert_expiring_today_sends_critical():
    repo = _make_repo()
    svc = _svc(repo)
    cfg = _make_config(days_until_expiry=0)
    await svc.notify_certificate_expiring(uuid.uuid4(), uuid.uuid4(), cfg)
    notif: FiscalNotification = repo.save.call_args[0][0]
    assert notif.severity == NotificationSeverity.CRITICAL
    assert "HOJE" in notif.title or "hoje" in notif.title.lower()


@pytest.mark.asyncio
async def test_cert_not_expiring_no_notification():
    """Certificado com mais de 30 dias não deve gerar notificação."""
    repo = _make_repo()
    svc = _svc(repo)
    cfg = _make_config(days_until_expiry=60)
    await svc.notify_certificate_expiring(uuid.uuid4(), uuid.uuid4(), cfg)
    repo.save.assert_not_called()


@pytest.mark.asyncio
async def test_cert_no_expiry_date_no_notification():
    repo = _make_repo()
    svc = _svc(repo)
    cfg = _make_config(days_until_expiry=None)
    await svc.notify_certificate_expiring(uuid.uuid4(), uuid.uuid4(), cfg)
    repo.save.assert_not_called()


@pytest.mark.asyncio
async def test_cert_dedupe_key_contains_fingerprint():
    repo = _make_repo()
    svc = _svc(repo)
    market_id = uuid.uuid4()
    cfg = _make_config(days_until_expiry=5, fingerprint="myfp123")
    cfg.market_id = market_id
    await svc.notify_certificate_expiring(uuid.uuid4(), market_id, cfg)
    notif: FiscalNotification = repo.save.call_args[0][0]
    assert "myfp123" in notif.dedupe_key
    assert str(market_id) in notif.dedupe_key


# ---------------------------------------------------------------------------
# notify_invoice_rejected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invoice_rejected_sends_warning():
    repo = _make_repo()
    svc = _svc(repo)
    sale_id = uuid.uuid4()
    await svc.notify_invoice_rejected(uuid.uuid4(), uuid.uuid4(), sale_id, "NCM inválido")
    repo.save.assert_called_once()
    notif: FiscalNotification = repo.save.call_args[0][0]
    assert notif.notification_type == NotificationType.INVOICE_REJECTED
    assert notif.severity == NotificationSeverity.WARNING


@pytest.mark.asyncio
async def test_invoice_rejected_message_contains_reason():
    repo = _make_repo()
    svc = _svc(repo)
    await svc.notify_invoice_rejected(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "NCM inválido")
    notif: FiscalNotification = repo.save.call_args[0][0]
    assert "NCM" in notif.message


@pytest.mark.asyncio
async def test_invoice_rejected_has_no_dedupe_key():
    """Cada rejeição é única — não deve ter dedupe."""
    repo = _make_repo()
    svc = _svc(repo)
    await svc.notify_invoice_rejected(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "Erro SEFAZ")
    notif: FiscalNotification = repo.save.call_args[0][0]
    assert notif.dedupe_key is None


@pytest.mark.asyncio
async def test_invoice_rejected_message_masks_sale_id():
    """Mensagem de rejeição não deve expor UUID completo desnecessariamente."""
    repo = _make_repo()
    svc = _svc(repo)
    sale_id = uuid.uuid4()
    await svc.notify_invoice_rejected(uuid.uuid4(), uuid.uuid4(), sale_id, "Erro")
    notif: FiscalNotification = repo.save.call_args[0][0]
    # A implementação mostra apenas 8 chars do sale_id seguido de "..."
    assert str(sale_id) not in notif.message or "..." in notif.message


# ---------------------------------------------------------------------------
# get_unread / mark_read
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_unread_delegates_to_repo():
    notif = FiscalNotification(
        owner_id=uuid.uuid4(),
        market_id=uuid.uuid4(),
        notification_type=NotificationType.FISCAL_LIMIT_80,
        severity=NotificationSeverity.WARNING,
        title="Test",
        message="Test msg",
    )
    repo = _make_repo()
    repo.list_unread.return_value = [notif]
    svc = _svc(repo)
    result = await svc.get_unread(uuid.uuid4())
    assert result == [notif]
    repo.list_unread.assert_called_once()


@pytest.mark.asyncio
async def test_mark_read_delegates_to_repo():
    repo = _make_repo()
    svc = _svc(repo)
    notif_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    await svc.mark_read(notif_id, owner_id)
    repo.mark_read.assert_called_once_with(notif_id, owner_id)
