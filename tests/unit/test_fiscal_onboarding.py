"""
PR13 — Testes do FiscalOnboardingService.

Cobre:
- Sem config: todos os passos pendentes, overall=incomplete
- Config completa mas sem teste: overall=ready_for_test
- Config completa + emissão de teste: overall=ready_for_production
- Campos obrigatórios faltando: dados_fiscais=pending com detail
- Certificado ausente: certificado_a1=pending
- Certificado expirado: certificado_a1=pending (error)
- Certificado expirando em <30d: certificado_a1=warning
- CSC ausente: csc=pending
- Série ausente: serie_numeracao=pending
- Sem perfis mas com NCM default: perfil_tributario=ok
- Sem perfis e sem NCM default: perfil_tributario=warning (não bloqueia produção)
- Com profiles no repo: perfil_tributario=ok
- Emissão de teste presente: emissao_teste=ok
- Produção ativa + validated: ambiente_producao=ok
- completion_pct: relação de okx/total
- can_activate_production: só quando all_required_ok + test_done + cfg existe
- missing_required: contém apenas chaves de itens required com status != ok/warning
"""
import os
import sys
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.services.fiscal.fiscal_onboarding_service import (
    FiscalOnboardingService,
    OnboardingStatus,
)
from domain.fiscal import (
    FiscalDocumentStatus,
    FiscalEnvironment,
    FiscalTenantConfig,
    TaxRegime,
    ValidationStatus,
)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _make_config(**kw) -> FiscalTenantConfig:
    future = datetime.utcnow() + timedelta(days=365)
    defaults = dict(
        market_id=uuid.uuid4(),
        cnpj="12345678000195",
        legal_name="Empresa Teste LTDA",
        tax_regime=TaxRegime.SIMPLES_NACIONAL,
        address_json={"logradouro": "Rua A", "numero": "1"},
        certificate_storage_key="fiscal/cert/abc.pfx",
        certificate_password_ciphertext="cipher_abc",
        certificate_valid_until=future,
        csc_id_ciphertext="cipher_cscid",
        csc_token_ciphertext="cipher_csctoken",
        nfce_series=1,
        enabled=False,
        environment=FiscalEnvironment.HOMOLOGATION,
        validation_status=ValidationStatus.NOT_VALIDATED,
    )
    defaults.update(kw)
    return FiscalTenantConfig(**defaults)


def _make_service(cfg=None, docs_count=0, profiles_count=0):
    config_repo = AsyncMock()
    config_repo.get_by_market.return_value = cfg

    doc_repo = AsyncMock()
    if docs_count > 0:
        doc_repo.list_by_market.return_value = (
            [object() for _ in range(docs_count)],
            docs_count,
        )
    else:
        doc_repo.list_by_market.return_value = ([], 0)

    tax_profile_repo = AsyncMock()
    tax_profile_repo.list_by_market.return_value = [object() for _ in range(profiles_count)]

    return FiscalOnboardingService(
        config_repo=config_repo,
        doc_repo=doc_repo,
        tax_profile_repo=tax_profile_repo,
    )


# ---------------------------------------------------------------------------
# Status geral (overall_status)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_config_returns_incomplete():
    svc = _make_service(cfg=None)
    status = await svc.get_onboarding_status(uuid.uuid4())
    assert status.overall_status == "incomplete"


@pytest.mark.asyncio
async def test_complete_config_no_test_returns_ready_for_test():
    # emissao_teste is not required_for_production, so all required items ok → ready_for_test
    cfg = _make_config()
    svc = _make_service(cfg=cfg, docs_count=0)
    status = await svc.get_onboarding_status(uuid.uuid4())
    assert status.overall_status == "ready_for_test"
    assert status.can_activate_production is False  # test not done yet


@pytest.mark.asyncio
async def test_complete_config_with_test_returns_ready_for_production():
    cfg = _make_config()
    svc = _make_service(cfg=cfg, docs_count=1)
    status = await svc.get_onboarding_status(uuid.uuid4())
    assert status.overall_status == "ready_for_production"


# ---------------------------------------------------------------------------
# Dados fiscais
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_cnpj_marks_dados_fiscais_pending():
    cfg = _make_config(cnpj=None)
    svc = _make_service(cfg=cfg)
    status = await svc.get_onboarding_status(uuid.uuid4())
    step = next(c for c in status.checklist if c.key == "dados_fiscais")
    assert step.status == "pending"
    assert "CNPJ" in (step.details or "")


@pytest.mark.asyncio
async def test_missing_legal_name_marks_dados_fiscais_pending():
    cfg = _make_config(legal_name=None)
    svc = _make_service(cfg=cfg)
    status = await svc.get_onboarding_status(uuid.uuid4())
    step = next(c for c in status.checklist if c.key == "dados_fiscais")
    assert step.status == "pending"
    assert "Razão Social" in (step.details or "")


@pytest.mark.asyncio
async def test_missing_tax_regime_marks_dados_fiscais_pending():
    cfg = _make_config(tax_regime=None)
    svc = _make_service(cfg=cfg)
    status = await svc.get_onboarding_status(uuid.uuid4())
    step = next(c for c in status.checklist if c.key == "dados_fiscais")
    assert step.status == "pending"


@pytest.mark.asyncio
async def test_all_dados_present_marks_ok():
    cfg = _make_config()
    svc = _make_service(cfg=cfg)
    status = await svc.get_onboarding_status(uuid.uuid4())
    step = next(c for c in status.checklist if c.key == "dados_fiscais")
    assert step.status == "ok"


# ---------------------------------------------------------------------------
# Certificado A1
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_certificate_marks_pending():
    cfg = _make_config(certificate_storage_key=None)
    svc = _make_service(cfg=cfg)
    status = await svc.get_onboarding_status(uuid.uuid4())
    step = next(c for c in status.checklist if c.key == "certificado_a1")
    assert step.status == "pending"


@pytest.mark.asyncio
async def test_expired_certificate_marks_pending_not_warning():
    past = datetime.utcnow() - timedelta(days=1)
    cfg = _make_config(certificate_valid_until=past)
    svc = _make_service(cfg=cfg)
    status = await svc.get_onboarding_status(uuid.uuid4())
    step = next(c for c in status.checklist if c.key == "certificado_a1")
    assert step.status == "pending"
    assert "expirado" in (step.details or "").lower()


@pytest.mark.asyncio
async def test_certificate_expiring_soon_marks_warning():
    # Cert valid but expires within 30 days → status "warning" (not "ok", not "pending")
    soon = datetime.utcnow() + timedelta(days=15)
    cfg = _make_config(certificate_valid_until=soon)
    svc = _make_service(cfg=cfg)
    status = await svc.get_onboarding_status(uuid.uuid4())
    step = next(c for c in status.checklist if c.key == "certificado_a1")
    assert step.status == "warning"
    assert "dias" in (step.details or "")


@pytest.mark.asyncio
async def test_valid_certificate_marks_ok():
    cfg = _make_config()
    svc = _make_service(cfg=cfg)
    status = await svc.get_onboarding_status(uuid.uuid4())
    step = next(c for c in status.checklist if c.key == "certificado_a1")
    assert step.status == "ok"


# ---------------------------------------------------------------------------
# CSC
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_csc_marks_pending():
    cfg = _make_config(csc_id_ciphertext=None, csc_token_ciphertext=None)
    svc = _make_service(cfg=cfg)
    status = await svc.get_onboarding_status(uuid.uuid4())
    step = next(c for c in status.checklist if c.key == "csc")
    assert step.status == "pending"
    assert "CSC" in (step.details or "")


@pytest.mark.asyncio
async def test_partial_csc_marks_pending():
    cfg = _make_config(csc_id_ciphertext="id", csc_token_ciphertext=None)
    svc = _make_service(cfg=cfg)
    status = await svc.get_onboarding_status(uuid.uuid4())
    step = next(c for c in status.checklist if c.key == "csc")
    assert step.status == "pending"


@pytest.mark.asyncio
async def test_full_csc_marks_ok():
    cfg = _make_config()
    svc = _make_service(cfg=cfg)
    status = await svc.get_onboarding_status(uuid.uuid4())
    step = next(c for c in status.checklist if c.key == "csc")
    assert step.status == "ok"


# ---------------------------------------------------------------------------
# Série e numeração
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_series_marks_pending():
    cfg = _make_config(nfce_series=0)
    svc = _make_service(cfg=cfg)
    status = await svc.get_onboarding_status(uuid.uuid4())
    step = next(c for c in status.checklist if c.key == "serie_numeracao")
    assert step.status == "pending"


@pytest.mark.asyncio
async def test_series_set_marks_ok():
    cfg = _make_config(nfce_series=1)
    svc = _make_service(cfg=cfg)
    status = await svc.get_onboarding_status(uuid.uuid4())
    step = next(c for c in status.checklist if c.key == "serie_numeracao")
    assert step.status == "ok"


# ---------------------------------------------------------------------------
# Perfil tributário (não obrigatório para produção)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_profiles_no_default_ncm_marks_warning():
    cfg = _make_config(default_ncm=None)
    svc = _make_service(cfg=cfg, profiles_count=0)
    status = await svc.get_onboarding_status(uuid.uuid4())
    step = next(c for c in status.checklist if c.key == "perfil_tributario")
    assert step.status == "warning"
    assert step.required_for_production is False


@pytest.mark.asyncio
async def test_with_profiles_marks_ok():
    cfg = _make_config()
    svc = _make_service(cfg=cfg, profiles_count=2)
    status = await svc.get_onboarding_status(uuid.uuid4())
    step = next(c for c in status.checklist if c.key == "perfil_tributario")
    assert step.status == "ok"


@pytest.mark.asyncio
async def test_no_profiles_but_default_ncm_marks_ok():
    cfg = _make_config(default_ncm="22021000", default_cfop="5102")
    svc = _make_service(cfg=cfg, profiles_count=0)
    status = await svc.get_onboarding_status(uuid.uuid4())
    step = next(c for c in status.checklist if c.key == "perfil_tributario")
    assert step.status == "ok"


# ---------------------------------------------------------------------------
# Emissão de teste
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_test_emission_marks_pending():
    cfg = _make_config()
    svc = _make_service(cfg=cfg, docs_count=0)
    status = await svc.get_onboarding_status(uuid.uuid4())
    step = next(c for c in status.checklist if c.key == "emissao_teste")
    assert step.status == "pending"


@pytest.mark.asyncio
async def test_with_test_emission_marks_ok():
    cfg = _make_config()
    svc = _make_service(cfg=cfg, docs_count=3)
    status = await svc.get_onboarding_status(uuid.uuid4())
    step = next(c for c in status.checklist if c.key == "emissao_teste")
    assert step.status == "ok"
    assert "3" in (step.details or "")


@pytest.mark.asyncio
async def test_emissao_teste_without_cfg_marks_pending():
    svc = _make_service(cfg=None)
    status = await svc.get_onboarding_status(uuid.uuid4())
    step = next(c for c in status.checklist if c.key == "emissao_teste")
    assert step.status == "pending"


# ---------------------------------------------------------------------------
# Ambiente de produção
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_production_active_and_validated_marks_ok():
    cfg = _make_config(
        environment=FiscalEnvironment.PRODUCTION,
        validation_status=ValidationStatus.VALIDATED,
        enabled=True,
    )
    svc = _make_service(cfg=cfg, docs_count=1)
    status = await svc.get_onboarding_status(uuid.uuid4())
    step = next(c for c in status.checklist if c.key == "ambiente_producao")
    assert step.status == "ok"
    assert step.required_for_production is False


@pytest.mark.asyncio
async def test_production_not_active_marks_pending():
    cfg = _make_config(
        environment=FiscalEnvironment.HOMOLOGATION,
        enabled=False,
    )
    svc = _make_service(cfg=cfg)
    status = await svc.get_onboarding_status(uuid.uuid4())
    step = next(c for c in status.checklist if c.key == "ambiente_producao")
    assert step.status == "pending"


# ---------------------------------------------------------------------------
# can_activate_production e missing_required
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_can_activate_only_when_all_ok_and_test_done():
    cfg = _make_config()
    svc = _make_service(cfg=cfg, docs_count=1)
    status = await svc.get_onboarding_status(uuid.uuid4())
    assert status.can_activate_production is True


@pytest.mark.asyncio
async def test_cannot_activate_without_config():
    svc = _make_service(cfg=None)
    status = await svc.get_onboarding_status(uuid.uuid4())
    assert status.can_activate_production is False


@pytest.mark.asyncio
async def test_cannot_activate_without_test_emission():
    cfg = _make_config()
    svc = _make_service(cfg=cfg, docs_count=0)
    status = await svc.get_onboarding_status(uuid.uuid4())
    assert status.can_activate_production is False


@pytest.mark.asyncio
async def test_missing_required_contains_failing_keys():
    cfg = _make_config(cnpj=None, csc_id_ciphertext=None, csc_token_ciphertext=None)
    svc = _make_service(cfg=cfg)
    status = await svc.get_onboarding_status(uuid.uuid4())
    assert "dados_fiscais" in status.missing_required
    assert "csc" in status.missing_required


@pytest.mark.asyncio
async def test_missing_required_empty_when_all_required_items_ok():
    # emissao_teste is not required_for_production, so config alone suffices for missing_required=[]
    cfg = _make_config()
    svc = _make_service(cfg=cfg, docs_count=0)
    status = await svc.get_onboarding_status(uuid.uuid4())
    assert status.missing_required == []


@pytest.mark.asyncio
async def test_missing_required_excludes_optional_items():
    """perfil_tributario e ambiente_producao são opcionais — não devem aparecer em missing_required."""
    cfg = _make_config(default_ncm=None)
    svc = _make_service(cfg=cfg, profiles_count=0)
    status = await svc.get_onboarding_status(uuid.uuid4())
    assert "perfil_tributario" not in status.missing_required
    assert "ambiente_producao" not in status.missing_required


# ---------------------------------------------------------------------------
# completion_pct
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_completion_pct_zero_when_no_config():
    svc = _make_service(cfg=None)
    status = await svc.get_onboarding_status(uuid.uuid4())
    assert status.completion_pct == 0


@pytest.mark.asyncio
async def test_completion_pct_100_when_all_ok():
    cfg = _make_config(
        environment=FiscalEnvironment.PRODUCTION,
        validation_status=ValidationStatus.VALIDATED,
        enabled=True,
        default_ncm="22021000",
    )
    svc = _make_service(cfg=cfg, docs_count=1, profiles_count=1)
    status = await svc.get_onboarding_status(uuid.uuid4())
    assert status.completion_pct == 100


@pytest.mark.asyncio
async def test_completion_pct_is_proportion_of_ok_steps():
    cfg = _make_config()  # 5 steps ok (sem emissao_teste e ambiente_producao)
    svc = _make_service(cfg=cfg, docs_count=0, profiles_count=0)
    status = await svc.get_onboarding_status(uuid.uuid4())
    # Contar quantos são "ok" manualmente
    ok_count = sum(1 for c in status.checklist if c.status == "ok")
    total = len(status.checklist)
    expected_pct = round((ok_count / total) * 100)
    assert status.completion_pct == expected_pct


# ---------------------------------------------------------------------------
# Estrutura do checklist
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_checklist_has_seven_items():
    svc = _make_service(cfg=None)
    status = await svc.get_onboarding_status(uuid.uuid4())
    assert len(status.checklist) == 7


@pytest.mark.asyncio
async def test_checklist_keys_are_correct():
    expected_keys = {
        "dados_fiscais", "certificado_a1", "csc",
        "serie_numeracao", "perfil_tributario",
        "emissao_teste", "ambiente_producao",
    }
    svc = _make_service(cfg=None)
    status = await svc.get_onboarding_status(uuid.uuid4())
    actual_keys = {c.key for c in status.checklist}
    assert actual_keys == expected_keys


# ---------------------------------------------------------------------------
# Validações estritas do Neectify Fiscal
# ---------------------------------------------------------------------------

def test_validate_cnpj_mathematically():
    from domain.validators import validate_cnpj
    
    # CNPJ válido de teste (matematicamente correto)
    assert validate_cnpj("12345678000195") is True
    assert validate_cnpj("12.345.678/0001-95") is True
    
    # CNPJ com dígitos errados
    assert validate_cnpj("12345678000190") is False
    # CNPJs com todos os dígitos iguais
    assert validate_cnpj("11111111111111") is False
    # Tamanho inválido
    assert validate_cnpj("12345") is False


def test_validate_for_production_neectify_strict():
    # 1. Com CNPJ inválido
    cfg = _make_config(provider="neectify_fiscal", cnpj="12345678000190")
    errors = cfg.validate_for_production()
    assert any("CNPJ inválido" in e for e in errors)
    
    # 2. Com UF fora de GO
    cfg = _make_config(
        provider="neectify_fiscal",
        cnpj="12345678000195",
        address_json={"uf": "SP", "city_code": "3550308"}  # São Paulo
    )
    errors = cfg.validate_for_production()
    assert any("Goiás (UF: 'GO')" in e for e in errors)
    
    # 3. Com município não homologado em GO
    cfg = _make_config(
        provider="neectify_fiscal",
        cnpj="12345678000195",
        address_json={"uf": "GO", "city_code": "5200050"}  # Abadia de Goiás
    )
    errors = cfg.validate_for_production()
    assert any("Município não suportado" in e for e in errors)
    
    # 4. Totalmente correto (Goiânia)
    cfg = _make_config(
        provider="neectify_fiscal",
        cnpj="12345678000195",
        address_json={"uf": "GO", "city_code": "5208707"}  # Goiânia
    )
    errors = cfg.validate_for_production()
    assert not any("CNPJ" in e or "Goiás" in e or "Município" in e for e in errors)


@pytest.mark.asyncio
async def test_sync_issuer_neectify_strict_validations():
    from domain.fiscal import FiscalValidationError
    
    # 1. CNPJ inválido
    cfg = _make_config(provider="neectify_fiscal", cnpj="12345678000190")
    svc = _make_service(cfg=cfg)
    svc._neectify = AsyncMock()
    
    with pytest.raises(FiscalValidationError) as exc:
        await svc.sync_issuer(uuid.uuid4(), {})
    assert "CNPJ inválido matematicamente" in str(exc.value)
    
    # 2. Estado inválido (SP)
    cfg = _make_config(
        provider="neectify_fiscal",
        cnpj="12345678000195",
        address_json={"uf": "SP", "city_code": "3550308"}
    )
    svc = _make_service(cfg=cfg)
    svc._neectify = AsyncMock()
    
    with pytest.raises(FiscalValidationError) as exc:
        await svc.sync_issuer(uuid.uuid4(), {})
    assert "Estado não suportado" in str(exc.value)
    
    # 3. Município não homologado em GO
    cfg = _make_config(
        provider="neectify_fiscal",
        cnpj="12345678000195",
        address_json={"uf": "GO", "city_code": "5200050"}
    )
    svc = _make_service(cfg=cfg)
    svc._neectify = AsyncMock()
    
    with pytest.raises(FiscalValidationError) as exc:
        await svc.sync_issuer(uuid.uuid4(), {})
    assert "Município não homologado" in str(exc.value)


def test_build_issuer_payload_cleaning_and_consistency():
    from application.services.fiscal.fiscal_onboarding_service import _build_issuer_payload
    
    cfg = _make_config(
        provider="neectify_fiscal",
        cnpj="12.345.678/0001-95",
        address_json={
            "uf": "GO",
            "city_code": "5208707",
            "city_name": "Goiânia",
            "zip_code": "74.223-010",
            "street": "Avenida 85"
        }
    )
    
    payload = _build_issuer_payload(cfg, {})
    
    # Valida limpeza de strings
    assert payload["cnpj"] == "12345678000195"
    assert payload["address"]["zip_code"] == "74223010"
    
    # Valida consistência absoluta de endereço (raiz vs address aninhado)
    assert payload["uf"] == "GO"
    assert payload["address"]["uf"] == "GO"
    assert payload["municipality_code"] == "5208707"
    assert payload["address"]["city_code"] == "5208707"
    assert payload["municipality_name"] == "Goiânia"
    assert payload["address"]["city_name"] == "Goiânia"

