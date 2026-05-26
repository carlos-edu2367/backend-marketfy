"""
PR1 — Testes de domínio fiscal: entidades, estados, regras de negócio.

Cobre:
- FiscalDocument: estado, transições, can_retry, is_terminal
- FiscalTenantConfig: validate_for_production, is_ready_for_emission
- FiscalUsageCounter: cota disponível, percentage, bloqueio
- FiscalEmissionPackage: validade
- FiscalNotification: mark_read
"""
import os
import sys
import uuid
from datetime import datetime, timedelta

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from domain.fiscal import (
    FiscalDocument,
    FiscalDocumentStatus,
    FiscalEmissionPackage,
    FiscalEnvironment,
    FiscalNotification,
    FiscalTenantConfig,
    FiscalUsageCounter,
    NotificationSeverity,
    NotificationType,
    TaxRegime,
    ValidationStatus,
)


# ---------------------------------------------------------------------------
# FiscalDocument
# ---------------------------------------------------------------------------

def _make_doc(**kw) -> FiscalDocument:
    return FiscalDocument(
        market_id=uuid.uuid4(),
        sale_id=uuid.uuid4(),
        **kw,
    )


def test_new_document_status_is_queued():
    doc = _make_doc()
    assert doc.status == FiscalDocumentStatus.QUEUED


def test_set_authorized_transitions_status():
    doc = _make_doc()
    doc.set_authorized("ACCESS_KEY_44", "PROTOCOL", 42, 1, "100", "Autorizado")
    assert doc.status == FiscalDocumentStatus.AUTHORIZED
    assert doc.access_key == "ACCESS_KEY_44"
    assert doc.protocol == "PROTOCOL"
    assert doc.number == 42
    assert doc.authorized_at is not None


def test_set_rejected_transitions_status():
    doc = _make_doc()
    doc.set_rejected("225", "NCM inválido")
    assert doc.status == FiscalDocumentStatus.REJECTED
    assert doc.sefaz_status_code == "225"


def test_set_provider_error():
    doc = _make_doc()
    doc.set_provider_error("Timeout no provedor")
    assert doc.status == FiscalDocumentStatus.PROVIDER_ERROR


def test_set_manual_action_required():
    doc = _make_doc()
    doc.set_manual_action_required("Requer intervenção manual")
    assert doc.status == FiscalDocumentStatus.MANUAL_ACTION_REQUIRED


def test_can_retry_for_retryable_statuses():
    for status in (
        FiscalDocumentStatus.QUEUED,
        FiscalDocumentStatus.PROVIDER_ERROR,
        FiscalDocumentStatus.SEFAZ_UNAVAILABLE,
    ):
        doc = _make_doc(status=status)
        assert doc.can_retry() is True, f"Expected can_retry for {status}"


def test_cannot_retry_terminal_statuses():
    for status in (
        FiscalDocumentStatus.AUTHORIZED,
        FiscalDocumentStatus.REJECTED,
        FiscalDocumentStatus.CANCELED,
        FiscalDocumentStatus.MANUAL_ACTION_REQUIRED,
    ):
        doc = _make_doc(status=status)
        assert doc.can_retry() is False, f"Expected no retry for {status}"


def test_is_terminal_for_terminal_statuses():
    for status in (
        FiscalDocumentStatus.AUTHORIZED,
        FiscalDocumentStatus.REJECTED,
        FiscalDocumentStatus.CANCELED,
        FiscalDocumentStatus.MANUAL_ACTION_REQUIRED,
        FiscalDocumentStatus.NOT_REQUESTED,
    ):
        doc = _make_doc(status=status)
        assert doc.is_terminal() is True, f"Expected terminal for {status}"


def test_is_not_terminal_for_transient_statuses():
    for status in (
        FiscalDocumentStatus.QUEUED,
        FiscalDocumentStatus.PROCESSING,
        FiscalDocumentStatus.PROVIDER_ERROR,
        FiscalDocumentStatus.SEFAZ_UNAVAILABLE,
    ):
        doc = _make_doc(status=status)
        assert doc.is_terminal() is False, f"Expected non-terminal for {status}"


# ---------------------------------------------------------------------------
# FiscalTenantConfig — validate_for_production
# ---------------------------------------------------------------------------

def _make_valid_config(**kw) -> FiscalTenantConfig:
    future = datetime.utcnow() + timedelta(days=365)
    defaults = dict(
        market_id=uuid.uuid4(),
        cnpj="12345678000195",
        legal_name="Empresa Teste LTDA",
        certificate_storage_key="fiscal/cert/abc.pfx",
        certificate_password_ciphertext="cipher_abc",
        certificate_valid_until=future,
        csc_id_ciphertext="cipher_cscid",
        csc_token_ciphertext="cipher_csctoken",
        tax_regime=TaxRegime.SIMPLES_NACIONAL,
        address_json={"logradouro": "Rua A", "numero": "1"},
        nfce_series=1,
        enabled=True,
        validation_status=ValidationStatus.VALIDATED,
    )
    defaults.update(kw)
    return FiscalTenantConfig(**defaults)


def test_valid_config_has_no_errors():
    cfg = _make_valid_config()
    errors = cfg.validate_for_production()
    assert errors == []


def test_missing_cnpj_produces_error():
    cfg = _make_valid_config(cnpj=None)
    errors = cfg.validate_for_production()
    assert any("CNPJ" in e for e in errors)


def test_missing_legal_name_produces_error():
    cfg = _make_valid_config(legal_name=None)
    errors = cfg.validate_for_production()
    assert any("Razão social" in e for e in errors)


def test_missing_certificate_produces_error():
    cfg = _make_valid_config(certificate_storage_key=None)
    errors = cfg.validate_for_production()
    assert any("Certificado" in e for e in errors)


def test_missing_csc_produces_error():
    cfg = _make_valid_config(csc_id_ciphertext=None, csc_token_ciphertext=None)
    errors = cfg.validate_for_production()
    assert any("CSC" in e for e in errors)


def test_expired_certificate_produces_error():
    past = datetime.utcnow() - timedelta(days=1)
    cfg = _make_valid_config(certificate_valid_until=past)
    errors = cfg.validate_for_production()
    assert any("vencido" in e.lower() for e in errors)


def test_missing_address_produces_error():
    cfg = _make_valid_config(address_json=None)
    errors = cfg.validate_for_production()
    assert any("Endereço" in e for e in errors)


def test_is_ready_for_emission_requires_enabled_and_certs():
    cfg = _make_valid_config()
    assert cfg.is_ready_for_emission() is True


def test_is_not_ready_when_disabled():
    cfg = _make_valid_config(enabled=False)
    assert cfg.is_ready_for_emission() is False


def test_is_not_ready_without_certificate():
    cfg = _make_valid_config(certificate_storage_key=None)
    assert cfg.is_ready_for_emission() is False


def test_is_not_ready_without_csc():
    cfg = _make_valid_config(csc_id_ciphertext=None)
    assert cfg.is_ready_for_emission() is False


def test_days_until_cert_expiry_future():
    cfg = _make_valid_config(
        certificate_valid_until=datetime.utcnow() + timedelta(days=30)
    )
    days = cfg.days_until_cert_expiry()
    assert 28 <= days <= 30


def test_days_until_cert_expiry_none_when_no_cert():
    cfg = _make_valid_config(certificate_valid_until=None)
    assert cfg.days_until_cert_expiry() is None


# ---------------------------------------------------------------------------
# FiscalUsageCounter
# ---------------------------------------------------------------------------

def _make_counter(**kw) -> FiscalUsageCounter:
    defaults = dict(
        owner_id=uuid.uuid4(),
        market_id=uuid.uuid4(),
        period_yyyymm="202506",
        included_limit=500,
        addon_limit=0,
        reserved_count=0,
        used_count=0,
    )
    defaults.update(kw)
    return FiscalUsageCounter(**defaults)


def test_available_quota_full():
    counter = _make_counter(included_limit=500, reserved_count=0)
    assert counter.available_quota() == 500


def test_available_quota_after_reservation():
    counter = _make_counter(included_limit=500, reserved_count=200)
    assert counter.available_quota() == 300


def test_available_quota_zero_when_exhausted():
    counter = _make_counter(included_limit=500, reserved_count=500)
    assert counter.available_quota() == 0


def test_available_quota_includes_addon():
    counter = _make_counter(included_limit=500, addon_limit=100, reserved_count=500)
    assert counter.available_quota() == 100


def test_usage_percentage_zero_when_no_use():
    counter = _make_counter(included_limit=500, reserved_count=0)
    assert counter.usage_percentage() == 0.0


def test_usage_percentage_at_80():
    counter = _make_counter(included_limit=500, reserved_count=400)
    assert counter.usage_percentage() == pytest.approx(80.0)


def test_usage_percentage_at_100():
    counter = _make_counter(included_limit=500, reserved_count=500)
    assert counter.usage_percentage() == pytest.approx(100.0)


def test_usage_percentage_100_when_limit_zero():
    counter = _make_counter(included_limit=0, reserved_count=0)
    assert counter.usage_percentage() == 100.0


def test_is_blocked_false_by_default():
    counter = _make_counter()
    assert counter.is_blocked() is False


def test_is_blocked_when_blocked_at_set():
    counter = _make_counter(blocked_at=datetime.utcnow())
    assert counter.is_blocked() is True


# ---------------------------------------------------------------------------
# FiscalEmissionPackage
# ---------------------------------------------------------------------------

def test_valid_package_is_valid():
    pkg = FiscalEmissionPackage(
        owner_id=uuid.uuid4(),
        quantity=100,
        remaining=50,
        valid_from=datetime.utcnow() - timedelta(days=1),
        valid_until=datetime.utcnow() + timedelta(days=30),
        payment_status="paid",
    )
    assert pkg.is_valid() is True


def test_empty_remaining_not_valid():
    pkg = FiscalEmissionPackage(
        owner_id=uuid.uuid4(),
        quantity=100,
        remaining=0,
        payment_status="paid",
    )
    assert pkg.is_valid() is False


def test_unpaid_package_not_valid():
    pkg = FiscalEmissionPackage(
        owner_id=uuid.uuid4(),
        quantity=100,
        remaining=50,
        payment_status="pending",
    )
    assert pkg.is_valid() is False


def test_expired_package_not_valid():
    pkg = FiscalEmissionPackage(
        owner_id=uuid.uuid4(),
        quantity=100,
        remaining=50,
        valid_until=datetime.utcnow() - timedelta(days=1),
        payment_status="paid",
    )
    assert pkg.is_valid() is False


# ---------------------------------------------------------------------------
# FiscalNotification
# ---------------------------------------------------------------------------

def test_notification_mark_read():
    notif = FiscalNotification(
        owner_id=uuid.uuid4(),
        market_id=uuid.uuid4(),
        notification_type=NotificationType.FISCAL_LIMIT_80,
        severity=NotificationSeverity.WARNING,
        title="80% atingido",
        message="Você usou 80% da cota.",
    )
    assert notif.read_at is None
    notif.mark_read()
    assert notif.read_at is not None
