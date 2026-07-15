"""
Router fiscal — endpoints de configuração, emissão, status, artefatos e pendências.

Organização:
- /fiscal/{market_id}/config              — GET/POST configuração fiscal
- /fiscal/{market_id}/config/validate     — POST validar configuração
- /fiscal/{market_id}/sales/{sale_id}/emit — POST solicitar emissão
- /fiscal/{market_id}/sales/{sale_id}/fiscal — GET status fiscal
- /fiscal/{market_id}/documents           — GET listagem de documentos
- /fiscal/{market_id}/documents/{doc_id}  — GET detalhe
- /fiscal/{market_id}/documents/{doc_id}/reprocess — POST reprocessar
- /fiscal/{market_id}/documents/{doc_id}/cancel    — POST cancelar
- /fiscal/{market_id}/notifications       — GET notificações
- /fiscal/{market_id}/tax-profiles        — GET/POST perfis fiscais
- /fiscal/artifacts/download              — GET download assinado
"""
from __future__ import annotations

import uuid
import os
from datetime import date, datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form, Query
from fastapi.responses import Response

from infra.web.dependencies import get_current_user, require_market_access, get_audit_service
from infra.security.market_access import MarketPermission
from infra.security.fiscal_rule_authorization import TaxRuleApprovalEvidenceError, assert_tax_rule_approver
from application.services.audit_service import AuditService
from application.dtos import (
    ProductTaxRuleDraftDTO,
    ProductTaxRuleDraftUpdateDTO,
    ProductTaxRulePublishDTO,
    ProductTaxRuleSuccessorDTO,
)
from domain.identity import User
from domain.shared import BusinessRuleException
from domain.fiscal import FiscalAuthError, FiscalValidationError
from infra.observability.audit import record_audit_event
from infra.observability.metrics import metrics_registry
from infra.config.logger import get_logger
from infra.security.uploads import validate_pfx_upload

logger = get_logger("fiscal_router")
router = APIRouter()


async def _require_tax_rule_accountant(*, db: AsyncSession, market_id: uuid.UUID, current_user: User) -> None:
    """Publication is deliberately stricter than general fiscal write access."""
    from infra.repositories.market_member_repo import SQLAlchemyMarketMemberRepository

    member = await SQLAlchemyMarketMemberRepository(db).get_active_member(market_id, current_user.id)
    try:
        if member is None:
            raise TaxRuleApprovalEvidenceError()
        assert_tax_rule_approver(member.role)
    except TaxRuleApprovalEvidenceError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)})


# =============================================================================
# DEPENDÊNCIAS LOCAIS
# =============================================================================

def _get_config_service(db=None):
    """Fábrica do FiscalConfigService."""
    from sqlalchemy.ext.asyncio import AsyncSession
    from infra.repositories.fiscal_repo import SQLAlchemyFiscalTenantConfigRepository
    from infra.security.secret_cipher import SecretCipher
    from infra.storage.fiscal_artifact_storage import FiscalArtifactStorage
    from infra.config.settings import get_settings
    from application.services.fiscal.fiscal_config_service import FiscalConfigService
    settings = get_settings()
    cipher = SecretCipher(settings.FISCAL_SECRET_KEY or settings.SECRET_KEY)
    storage = FiscalArtifactStorage()
    repo = SQLAlchemyFiscalTenantConfigRepository(db)
    return FiscalConfigService(config_repo=repo, cipher=cipher, storage=storage)


def _get_emission_service(db, arq_pool=None):
    from infra.repositories.fiscal_repo import (
        SQLAlchemyFiscalDocumentRepository,
        SQLAlchemyFiscalEventRepository,
        SQLAlchemyFiscalTenantConfigRepository,
        SQLAlchemyFiscalUsageRepository,
    )
    from infra.repositories.sqlalchemy_repos import (
        SQLAlchemySaleRepository,
        SQLAlchemyMarketRepository,
        SQLAlchemyUserRepository,
        SQLAlchemyPlanRepository,
    )
    from infra.repositories.billing_repo import SQLAlchemyBillingSubscriptionRepository
    from application.services.fiscal.fiscal_emission_service import FiscalEmissionService
    from application.services.fiscal.fiscal_pre_validator import FiscalPreValidator
    from application.services.fiscal.fiscal_quota_service import FiscalQuotaService
    from application.services.plan_access_service import PlanAccessService

    usage_repo = SQLAlchemyFiscalUsageRepository(db)
    quota_svc = FiscalQuotaService(usage_repo)
    validator = FiscalPreValidator()
    plan_access = PlanAccessService(
        user_repo=SQLAlchemyUserRepository(db),
        plan_repo=SQLAlchemyPlanRepository(db),
        subscription_repo=SQLAlchemyBillingSubscriptionRepository(db),
    )
    return FiscalEmissionService(
        config_repo=SQLAlchemyFiscalTenantConfigRepository(db),
        doc_repo=SQLAlchemyFiscalDocumentRepository(db),
        event_repo=SQLAlchemyFiscalEventRepository(db),
        quota_service=quota_svc,
        pre_validator=validator,
        sale_repo=SQLAlchemySaleRepository(db),
        market_repo=SQLAlchemyMarketRepository(db),
        arq_pool=arq_pool,
        plan_access_service=plan_access,
    )


def _get_arq_pool(request: Request):
    """Obtém pool ARQ da app state, se disponível."""
    return getattr(request.app.state, "arq_pool", None)


# =============================================================================
# CONFIGURAÇÃO FISCAL
# =============================================================================

from sqlalchemy.ext.asyncio import AsyncSession
from infra.database.setup import get_db


@router.get("/{market_id}/config", tags=["fiscal"])
async def get_config(
    market_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    market=Depends(require_market_access(MarketPermission.FISCAL_READ)),
):
    """Retorna configuração fiscal atual (sem segredos)."""
    from infra.config.settings import get_settings
    settings = get_settings()
    svc = _get_config_service(db)
    cfg = await svc.get_config(market_id)
    if not cfg:
        return {
            "market_id": str(market_id),
            "enabled": False,
            "environment": "homologacao",
            "provider": settings.FISCAL_PROVIDER,
            "validation_status": "not_validated",
            "has_certificate": False,
            "has_csc": False,
            "certificate_valid_until": None,
        }

    csc_id_decrypted = None
    if cfg.csc_id_ciphertext:
        try:
            csc_id_decrypted = svc.cipher.decrypt(cfg.csc_id_ciphertext)
        except Exception:
            csc_id_decrypted = None

    return {
        "market_id": str(market_id),
        "enabled": cfg.enabled,
        "environment": cfg.environment.value,
        "provider": cfg.provider,
        "legal_name": cfg.legal_name,
        "trade_name": cfg.trade_name,
        "cnpj": cfg.cnpj,
        "state_registration": cfg.state_registration,
        "crt": cfg.crt,
        "address_json": cfg.address_json,
        "csc_id": csc_id_decrypted,
        "tax_regime": cfg.tax_regime.value if cfg.tax_regime else None,
        "nfce_series": cfg.nfce_series,
        "default_cfop": cfg.default_cfop,
        "default_ncm": cfg.default_ncm,
        "default_csosn": cfg.default_csosn,
        "has_certificate": bool(cfg.certificate_storage_key),
        "certificate_fingerprint": cfg.certificate_fingerprint,
        "certificate_valid_until": cfg.certificate_valid_until.isoformat() if cfg.certificate_valid_until else None,
        "has_csc": bool(cfg.csc_id_ciphertext and cfg.csc_token_ciphertext),
        "validation_status": cfg.validation_status.value,
        "last_validated_at": cfg.last_validated_at.isoformat() if cfg.last_validated_at else None,
        "numbering_mode": cfg.numbering_mode.value,
    }


@router.post("/{market_id}/config", tags=["fiscal"])
async def save_config(
    request: Request,
    market_id: uuid.UUID,
    # Dados do estabelecimento
    legal_name: Optional[str] = Form(None),
    trade_name: Optional[str] = Form(None),
    cnpj: Optional[str] = Form(None),
    state_registration: Optional[str] = Form(None),
    tax_regime: Optional[str] = Form(None),
    crt: Optional[str] = Form(None),
    # CSC
    csc_id: Optional[str] = Form(None),
    csc_token: Optional[str] = Form(None),
    # Certificado
    certificate_password: Optional[str] = Form(None),
    certificate_file: Optional[UploadFile] = File(None),
    # Configuração
    environment: str = Form("homologacao"),
    nfce_series: Optional[int] = Form(None),
    default_cfop: Optional[str] = Form(None),
    default_ncm: Optional[str] = Form(None),
    default_csosn: Optional[str] = Form(None),
    address: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    audit: AuditService = Depends(get_audit_service),
    market=Depends(require_market_access(MarketPermission.FISCAL_WRITE)),
):
    """Salva/atualiza configuração fiscal. Segredos write-only."""
    cert_bytes = None
    cert_filename = None

    if certificate_file:
        cert_bytes = await certificate_file.read()
        try:
            cert_filename = validate_pfx_upload(
                certificate_file.filename,
                certificate_file.content_type,
                len(cert_bytes),
                market_id,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))

    from infra.config.settings import get_settings
    settings = get_settings()

    data = {
        "legal_name": legal_name, "trade_name": trade_name, "cnpj": cnpj,
        "state_registration": state_registration, "tax_regime": tax_regime,
        "crt": crt, "csc_id": csc_id, "csc_token": csc_token,
        "certificate_password": certificate_password, "environment": environment,
        "nfce_series": nfce_series, "default_cfop": default_cfop,
        "default_ncm": default_ncm, "default_csosn": default_csosn,
        "provider": settings.FISCAL_PROVIDER,
    }
    # Remover None para não sobrescrever
    data = {k: v for k, v in data.items() if v is not None}

    if address:
        try:
            import json
            data["address"] = json.loads(address)
        except Exception:
            data["address"] = {"street": address}

    svc = _get_config_service(db)
    try:
        cfg = await svc.save_config(
            market_id=market_id,
            data=data,
            certificate_bytes=cert_bytes,
            certificate_filename=cert_filename,
        )
        await record_audit_event(
            audit, request, actor=current_user, action="fiscal.config.updated",
            resource_type="fiscal_tenant_config", result="success", market_id=market_id,
            metadata={"environment": environment, "has_certificate": bool(cert_bytes)},
        )
        return {"message": "Configuração fiscal salva.", "market_id": str(market_id)}
    except BusinessRuleException as e:
        raise HTTPException(400, str(e))


@router.post("/{market_id}/config/validate", tags=["fiscal"])
async def validate_config(
    request: Request,
    market_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    market=Depends(require_market_access(MarketPermission.FISCAL_WRITE)),
):
    """Valida configuração fiscal para produção."""
    svc = _get_config_service(db)
    errors = await svc.validate_config(market_id)
    return {
        "valid": len(errors) == 0,
        "errors": errors,
    }


@router.post("/{market_id}/config/enable", tags=["fiscal"])
async def enable_fiscal(
    request: Request,
    market_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    audit: AuditService = Depends(get_audit_service),
    market=Depends(require_market_access(MarketPermission.FISCAL_WRITE)),
):
    """Ativa emissão fiscal para o mercado. Exige validação prévia em produção."""
    svc = _get_config_service(db)
    try:
        cfg = await svc.enable_fiscal(market_id)
        await record_audit_event(
            audit, request, actor=current_user, action="fiscal.enabled",
            resource_type="fiscal_tenant_config", result="success", market_id=market_id,
        )
        return {"message": "Emissão fiscal ativada.", "enabled": cfg.enabled}
    except BusinessRuleException as e:
        raise HTTPException(400, str(e))


# =============================================================================
# EMISSÃO E STATUS
# =============================================================================

@router.post("/{market_id}/sales/{sale_id}/emit", tags=["fiscal"])
async def request_emission(
    request: Request,
    market_id: uuid.UUID,
    sale_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    audit: AuditService = Depends(get_audit_service),
    market=Depends(require_market_access(MarketPermission.FISCAL_WRITE)),
):
    """
    Solicita emissão de NFC-e para uma venda.
    Retorna imediatamente com status queued/processing.
    A emissão real ocorre no worker fiscal.
    """
    arq_pool = _get_arq_pool(request)
    if arq_pool is None:
        raise HTTPException(
            status_code=503,
            detail="Serviço de emissão indisponível (conexão com Redis falhou).",
        )
    svc = _get_emission_service(db, arq_pool)

    from domain.fiscal import FiscalQuotaExceededError
    try:
        result = await svc.request_emission(
            market_id=market_id,
            sale_id=sale_id,
            owner_id=current_user.id,
        )
        metrics_registry.record_fiscal_invoice(str(sale_id), "queued")
        await record_audit_event(
            audit, request, actor=current_user, action="fiscal.emission.requested",
            resource_type="fiscal_document", resource_id=result.get("fiscal_document_id"),
            result="success", market_id=market_id,
            metadata={"status": result.get("status")},
        )
        return result
    except FiscalQuotaExceededError as e:
        metrics_registry.record_fiscal_invoice(str(sale_id), "quota_exceeded")
        raise HTTPException(
            status_code=402,
            detail={
                "error": "quota_exceeded",
                "message": "Limite mensal de emissões NFC-e atingido. Adquira créditos extras para continuar.",
                "used": e.used,
                "included_limit": e.included_limit,
                "addon_limit": e.addon_limit,
            },
        )
    except BusinessRuleException as e:
        metrics_registry.record_fiscal_invoice(str(sale_id), "failed")
        raise HTTPException(400, str(e))


@router.get("/{market_id}/sales/{sale_id}/fiscal", tags=["fiscal"])
async def get_fiscal_status(
    market_id: uuid.UUID,
    sale_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    market=Depends(require_market_access(MarketPermission.FISCAL_READ)),
):
    """Retorna status fiscal atual de uma venda. Usado pelo PDV em polling."""
    svc = _get_emission_service(db)
    status = await svc.get_status(market_id, sale_id)
    if not status:
        return {"status": "not_requested", "message": "Nenhum documento fiscal para esta venda."}
    return status


@router.get("/{market_id}/sales/{sale_id}/nfce/print", tags=["fiscal"])
async def get_nfce_print_payload(
    market_id: uuid.UUID,
    sale_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    market=Depends(require_market_access(MarketPermission.FISCAL_READ)),
):
    """
    Retorna payload seguro de impressao DANFE NFC-e.

    Fonte de verdade: XML autorizado (`nfeProc`) do provedor fiscal, lido do
    storage privado ou baixado backend-to-backend quando o artefato ainda nao
    foi persistido pelo job assíncrono.
    """
    from infra.config.settings import get_settings
    from infra.providers.fiscal.provider_factory import get_fiscal_provider
    from infra.repositories.fiscal_repo import (
        SQLAlchemyFiscalArtifactRepository,
        SQLAlchemyFiscalDocumentRepository,
    )
    from infra.storage.fiscal_artifact_storage import FiscalArtifactStorage
    from application.services.fiscal.nfce_print_service import NfcePrintService

    settings = get_settings()
    provider_name = settings.FISCAL_PROVIDER
    provider = get_fiscal_provider(provider_name)
    api_token = settings.NEECTIFY_API_KEY or "" if provider_name == "neectify_fiscal" else ""

    service = NfcePrintService(
        doc_repo=SQLAlchemyFiscalDocumentRepository(db),
        artifact_repo=SQLAlchemyFiscalArtifactRepository(db),
        storage=FiscalArtifactStorage(),
        provider=provider,
        api_token=api_token,
    )
    try:
        return await service.prepare_sale_print_payload(market_id, sale_id)
    except BusinessRuleException as exc:
        raise HTTPException(status_code=409, detail=str(exc))


# =============================================================================
# LISTAGEM E GESTÃO DE DOCUMENTOS
# =============================================================================

@router.get("/{market_id}/documents", tags=["fiscal"])
async def list_documents(
    market_id: uuid.UUID,
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    market=Depends(require_market_access(MarketPermission.FISCAL_READ)),
):
    """Lista documentos fiscais do mercado com filtros."""
    from infra.repositories.fiscal_repo import SQLAlchemyFiscalDocumentRepository
    repo = SQLAlchemyFiscalDocumentRepository(db)
    docs, total = await repo.list_by_market(market_id, status=status, limit=limit, offset=offset)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_doc_to_dict(d) for d in docs],
    }


@router.get("/{market_id}/documents/{doc_id}", tags=["fiscal"])
async def get_document(
    market_id: uuid.UUID,
    doc_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    market=Depends(require_market_access(MarketPermission.FISCAL_READ)),
):
    """Detalhe de um documento fiscal."""
    from infra.repositories.fiscal_repo import (
        SQLAlchemyFiscalDocumentRepository,
        SQLAlchemyFiscalAttemptRepository,
        SQLAlchemyFiscalEventRepository,
    )
    doc_repo = SQLAlchemyFiscalDocumentRepository(db)
    doc = await doc_repo.get_by_id(doc_id)
    if not doc or str(doc.market_id) != str(market_id):
        raise HTTPException(404, "Documento fiscal não encontrado.")

    attempt_repo = SQLAlchemyFiscalAttemptRepository(db)
    event_repo = SQLAlchemyFiscalEventRepository(db)
    attempts = await attempt_repo.list_by_document(doc_id)
    events = await event_repo.list_by_document(doc_id)

    return {
        **_doc_to_dict(doc),
        "attempts": [
            {
                "attempt_number": a.attempt_number,
                "operation": a.operation.value,
                "status": a.status.value,
                "http_status": a.http_status,
                "duration_ms": a.duration_ms,
                "started_at": a.started_at.isoformat() if a.started_at else None,
            }
            for a in attempts
        ],
        "events": [
            {
                "event_type": e.event_type,
                "source": e.source.value,
                "message": e.message,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ],
    }


@router.post("/{market_id}/documents/{doc_id}/reprocess", tags=["fiscal"])
async def reprocess_document(
    request: Request,
    market_id: uuid.UUID,
    doc_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    audit: AuditService = Depends(get_audit_service),
    market=Depends(require_market_access(MarketPermission.FISCAL_WRITE)),
):
    """Recoloca documento pendente na fila para reprocessamento."""
    from infra.repositories.fiscal_repo import SQLAlchemyFiscalDocumentRepository
    from domain.fiscal import FiscalDocumentStatus

    doc_repo = SQLAlchemyFiscalDocumentRepository(db)
    doc = await doc_repo.get_by_id(doc_id)
    if not doc or str(doc.market_id) != str(market_id):
        raise HTTPException(404, "Documento fiscal não encontrado.")
    if doc.is_terminal() and doc.status != FiscalDocumentStatus.REJECTED:
        raise HTTPException(400, f"Documento em estado terminal '{doc.status.value}' não pode ser reprocessado.")

    doc.status = FiscalDocumentStatus.QUEUED
    await doc_repo.save(doc)

    arq_pool = _get_arq_pool(request)
    if arq_pool is None:
        raise HTTPException(
            status_code=503,
            detail="Serviço de emissão indisponível (conexão com Redis falhou).",
        )

    svc = _get_emission_service(db, arq_pool)
    try:
        await svc.reprocess_document(
            market_id=market_id,
            doc_id=doc_id,
            owner_id=current_user.id,
        )
    except BusinessRuleException as e:
        raise HTTPException(400, str(e))

    await record_audit_event(
        audit, request, actor=current_user, action="fiscal.document.reprocessed",
        resource_type="fiscal_document", resource_id=str(doc_id),
        result="success", market_id=market_id,
    )
    return {"message": "Documento recolocado na fila.", "doc_id": str(doc_id)}


@router.post("/{market_id}/documents/{doc_id}/cancel", tags=["fiscal"])
async def cancel_document(
    request: Request,
    market_id: uuid.UUID,
    doc_id: uuid.UUID,
    justification: str = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    audit: AuditService = Depends(get_audit_service),
    market=Depends(require_market_access(MarketPermission.FISCAL_WRITE)),
):
    """Cancela uma NFC-e autorizada dentro do prazo legal."""
    from infra.repositories.fiscal_repo import (
        SQLAlchemyFiscalDocumentRepository,
        SQLAlchemyFiscalEventRepository,
        SQLAlchemyFiscalTenantConfigRepository,
    )
    from infra.providers.fiscal.provider_factory import get_fiscal_provider
    from infra.security.secret_cipher import SecretCipher
    from infra.config.settings import get_settings
    from domain.fiscal import FiscalDocumentStatus, FiscalEvent, FiscalEventSource

    if len(justification) < 15:
        raise HTTPException(400, "Justificativa deve ter ao menos 15 caracteres.")

    doc_repo = SQLAlchemyFiscalDocumentRepository(db)
    doc = await doc_repo.get_by_id(doc_id)
    if not doc or str(doc.market_id) != str(market_id):
        raise HTTPException(404, "Documento não encontrado.")
    if doc.status != FiscalDocumentStatus.AUTHORIZED:
        raise HTTPException(400, "Apenas NFC-e autorizadas podem ser canceladas.")
    if not doc.access_key:
        raise HTTPException(400, "NFC-e sem chave de acesso — cancelamento impossível.")

    settings = get_settings()
    config_repo = SQLAlchemyFiscalTenantConfigRepository(db)
    cfg = await config_repo.get_by_market(market_id)
    if not cfg:
        raise HTTPException(400, "Configuração fiscal não encontrada.")

    cipher = SecretCipher(settings.FISCAL_SECRET_KEY or settings.SECRET_KEY)
    api_token = cipher.decrypt(cfg.csc_token_ciphertext or "") or \
                getattr(settings, "FOCUS_NFE_API_TOKEN", "")

    provider = get_fiscal_provider(settings.FISCAL_PROVIDER, cfg.environment.value)
    result = await provider.cancel_nfce(
        provider_ref=doc.provider_ref,
        access_key=doc.access_key,
        justification=justification,
        api_token=api_token,
        environment=cfg.environment.value,
    )

    if result.success:
        from datetime import datetime
        doc.status = FiscalDocumentStatus.CANCELED
        doc.canceled_at = datetime.utcnow()
        await doc_repo.save(doc)

        event_repo = SQLAlchemyFiscalEventRepository(db)
        event = FiscalEvent(
            fiscal_document_id=doc.id, market_id=doc.market_id,
            event_type="canceled", source=FiscalEventSource.MARKETFY,
            message=f"Cancelado por {current_user.name}. Protocolo: {result.protocol}",
        )
        await event_repo.append(event)

        await record_audit_event(
            audit, request, actor=current_user, action="fiscal.document.canceled",
            resource_type="fiscal_document", resource_id=str(doc_id),
            result="success", market_id=market_id,
        )
        return {"message": "NFC-e cancelada.", "protocol": result.protocol}
    else:
        raise HTTPException(400, result.error_message or "Falha no cancelamento.")


# =============================================================================
# NOTIFICAÇÕES
# =============================================================================

@router.get("/{market_id}/notifications", tags=["fiscal"])
async def get_notifications(
    market_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    market=Depends(require_market_access(MarketPermission.FISCAL_READ)),
):
    """Retorna notificações fiscais não lidas."""
    from infra.repositories.fiscal_repo import SQLAlchemyFiscalNotificationRepository
    from application.services.fiscal.fiscal_notification_service import FiscalNotificationService
    repo = SQLAlchemyFiscalNotificationRepository(db)
    svc = FiscalNotificationService(repo)
    notifs = await svc.get_unread(current_user.id)
    return {
        "total": len(notifs),
        "items": [
            {
                "id": str(n.id),
                "type": n.notification_type.value,
                "severity": n.severity.value,
                "title": n.title,
                "message": n.message,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in notifs
        ],
    }


@router.post("/{market_id}/notifications/{notif_id}/read", tags=["fiscal"])
async def mark_notification_read(
    market_id: uuid.UUID,
    notif_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    market=Depends(require_market_access(MarketPermission.FISCAL_READ)),
):
    from infra.repositories.fiscal_repo import SQLAlchemyFiscalNotificationRepository
    from application.services.fiscal.fiscal_notification_service import FiscalNotificationService
    repo = SQLAlchemyFiscalNotificationRepository(db)
    svc = FiscalNotificationService(repo)
    await svc.mark_read(notif_id, current_user.id)
    return {"message": "Notificação marcada como lida."}


# =============================================================================
# PERFIS FISCAIS DE PRODUTO
# =============================================================================

def _tax_rule_response(rule):
    """Keep the REST representation explicit and free of inferred values."""
    fields = (
        "id", "name", "version", "status", "effective_from", "effective_to", "ncm", "cest", "origin",
        "cfop", "icms_group", "icms_cst", "icms_csosn", "icms_mod_bc", "icms_rate",
        "icms_reduction_rate", "icms_st_mod_bc", "icms_st_mva_rate", "icms_st_rate", "fcp_rate",
        "pis_cst", "pis_rate", "cofins_cst", "cofins_rate", "approved_by", "approved_at",
        "created_at", "updated_at",
    )
    response = {field: getattr(rule, field) for field in fields}
    response["id"] = str(rule.id)
    response["status"] = rule.status.value
    response["approved_by"] = str(rule.approved_by) if rule.approved_by else None
    return response


@router.get("/{market_id}/tax-rules", tags=["fiscal"])
async def list_tax_rules(
    market_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    market=Depends(require_market_access(MarketPermission.FISCAL_READ)),
):
    from infra.repositories.fiscal_repo import SQLAlchemyProductTaxRuleRepository

    rules = await SQLAlchemyProductTaxRuleRepository(db).list_rules(market_id)
    return {"items": [_tax_rule_response(rule) for rule in rules]}


@router.post("/{market_id}/tax-rules", status_code=201, tags=["fiscal"])
async def create_tax_rule_draft(
    request: Request,
    market_id: uuid.UUID,
    dto: ProductTaxRuleDraftDTO,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    audit: AuditService = Depends(get_audit_service),
    market=Depends(require_market_access(MarketPermission.FISCAL_WRITE)),
):
    from domain.fiscal import ProductTaxRule
    from infra.repositories.fiscal_repo import SQLAlchemyProductTaxRuleRepository

    rule = ProductTaxRule(market_id=market_id, **dto.model_dump())
    saved = await SQLAlchemyProductTaxRuleRepository(db).create_draft(rule)
    await record_audit_event(
        audit, request, actor=current_user, action="fiscal.tax_rule.draft_created",
        resource_type="product_tax_rule", resource_id=str(saved.id), result="success", market_id=market_id,
    )
    return _tax_rule_response(saved)


@router.patch("/{market_id}/tax-rules/{rule_id}/draft", tags=["fiscal"])
async def update_tax_rule_draft(
    request: Request,
    market_id: uuid.UUID,
    rule_id: uuid.UUID,
    dto: ProductTaxRuleDraftUpdateDTO,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    audit: AuditService = Depends(get_audit_service),
    market=Depends(require_market_access(MarketPermission.FISCAL_WRITE)),
):
    from infra.repositories.fiscal_repo import SQLAlchemyProductTaxRuleRepository

    repo = SQLAlchemyProductTaxRuleRepository(db)
    rule = await repo.get_rule(market_id, rule_id)
    if rule is None:
        raise HTTPException(404, "Regra fiscal não encontrada.")
    changes = dto.model_dump(exclude_unset=True)
    saved = await repo.update_draft(rule, changes)
    await record_audit_event(
        audit, request, actor=current_user, action="fiscal.tax_rule.draft_updated",
        resource_type="product_tax_rule", resource_id=str(saved.id), result="success", market_id=market_id,
        metadata={"changed_fields": sorted(changes)},
    )
    return _tax_rule_response(saved)


@router.post("/{market_id}/tax-rules/{rule_id}/publish", tags=["fiscal"])
async def publish_tax_rule(
    request: Request,
    market_id: uuid.UUID,
    rule_id: uuid.UUID,
    dto: ProductTaxRulePublishDTO,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    audit: AuditService = Depends(get_audit_service),
    market=Depends(require_market_access(MarketPermission.FISCAL_WRITE)),
):
    from infra.repositories.fiscal_repo import SQLAlchemyProductTaxRuleRepository

    repo = SQLAlchemyProductTaxRuleRepository(db)
    rule = await repo.get_rule(market_id, rule_id)
    if rule is None:
        raise HTTPException(404, "Regra fiscal não encontrada.")
    await _require_tax_rule_accountant(db=db, market_id=market_id, current_user=current_user)
    from domain.fiscal import TaxRuleApproval

    # Approval identity and timestamp are server-derived; the caller only
    # references the accountant-approved homologation XML.
    rule.approved_by = current_user.id
    approval = TaxRuleApproval.from_authenticated_actor(
        rule_id=rule.id,
        accountant_user_id=current_user.id,
        homologation_xml_reference=dto.homologation_xml_reference,
    )
    rule.approved_at = approval.approved_at
    try:
        saved = await repo.publish_rule_with_approval(rule, approval)
    except BusinessRuleException as exc:
        raise HTTPException(400, str(exc))
    await record_audit_event(
        audit, request, actor=current_user, action="fiscal.tax_rule.published",
        resource_type="product_tax_rule", resource_id=str(saved.id), result="success", market_id=market_id,
        metadata={"approved_by": str(saved.approved_by), "approved_at": saved.approved_at.isoformat()},
    )
    return _tax_rule_response(saved)


@router.post("/{market_id}/tax-rules/{rule_id}/successor", status_code=201, tags=["fiscal"])
async def create_tax_rule_successor(
    request: Request,
    market_id: uuid.UUID,
    rule_id: uuid.UUID,
    dto: ProductTaxRuleSuccessorDTO,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    audit: AuditService = Depends(get_audit_service),
    market=Depends(require_market_access(MarketPermission.FISCAL_WRITE)),
):
    from infra.repositories.fiscal_repo import SQLAlchemyProductTaxRuleRepository

    repo = SQLAlchemyProductTaxRuleRepository(db)
    rule = await repo.get_rule(market_id, rule_id)
    if rule is None:
        raise HTTPException(404, "Regra fiscal não encontrada.")
    try:
        successor = await repo.create_successor(rule, effective_from=dto.effective_from)
    except BusinessRuleException as exc:
        raise HTTPException(400, str(exc))
    await record_audit_event(
        audit, request, actor=current_user, action="fiscal.tax_rule.successor_created",
        resource_type="product_tax_rule", resource_id=str(successor.id), result="success", market_id=market_id,
        metadata={"supersedes_rule_id": str(rule.id), "rule_family_id": str(successor.rule_family_id)},
    )
    return _tax_rule_response(successor)


@router.post("/{market_id}/tax-rules/{rule_id}/retire", tags=["fiscal"])
async def retire_tax_rule(
    request: Request,
    market_id: uuid.UUID,
    rule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    audit: AuditService = Depends(get_audit_service),
    market=Depends(require_market_access(MarketPermission.FISCAL_WRITE)),
):
    from infra.repositories.fiscal_repo import SQLAlchemyProductTaxRuleRepository

    repo = SQLAlchemyProductTaxRuleRepository(db)
    rule = await repo.get_rule(market_id, rule_id)
    if rule is None:
        raise HTTPException(404, "Regra fiscal não encontrada.")
    try:
        saved = await repo.retire_rule(rule)
    except BusinessRuleException as exc:
        raise HTTPException(400, str(exc))
    await record_audit_event(
        audit, request, actor=current_user, action="fiscal.tax_rule.retired",
        resource_type="product_tax_rule", resource_id=str(saved.id), result="success", market_id=market_id,
    )
    return _tax_rule_response(saved)


@router.get("/{market_id}/tax-rule-pendencies", tags=["fiscal"])
async def list_tax_rule_pendencies(
    market_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    market=Depends(require_market_access(MarketPermission.FISCAL_READ)),
):
    from infra.repositories.fiscal_repo import SQLAlchemyProductTaxRuleRepository
    from infra.repositories.sqlalchemy_repos import SQLAlchemyProductRepository

    products = await SQLAlchemyProductRepository(db).list_by_market(market_id)
    statuses = await SQLAlchemyProductTaxRuleRepository(db).list_product_fiscal_status(
        market_id, [product.id for product in products], date.today()
    )
    return {
        "items": [
            {"product_id": str(product.id), "product_name": product.name, "fiscal_status": statuses[product.id]}
            for product in products
        ]
    }

@router.get("/{market_id}/tax-profiles", tags=["fiscal"])
async def list_tax_profiles(
    market_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    market=Depends(require_market_access(MarketPermission.FISCAL_READ)),
):
    from infra.repositories.fiscal_repo import SQLAlchemyProductTaxProfileRepository
    repo = SQLAlchemyProductTaxProfileRepository(db)
    profiles = await repo.list_by_market(market_id)
    return {
        "legacy": True,
        "items": [
            {
                "id": str(p.id), "name": p.name, "ncm": p.ncm, "cfop": p.cfop,
                "icms_csosn": p.icms_csosn, "active": p.active,
            }
            for p in profiles
        ]
    }


@router.post("/{market_id}/tax-profiles", tags=["fiscal"])
async def create_tax_profile(
    request: Request,
    market_id: uuid.UUID,
    name: str = Form(...),
    ncm: Optional[str] = Form(None),
    cfop: str = Form("5102"),
    icms_csosn: str = Form("102"),
    pis_cst: str = Form("07"),
    cofins_cst: str = Form("07"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    market=Depends(require_market_access(MarketPermission.FISCAL_WRITE)),
):
    raise HTTPException(
        status_code=410,
        detail="Perfis fiscais legados são somente leitura. Crie uma regra fiscal versionada.",
    )


# =============================================================================
# INUTILIZAÇÃO DE NUMERAÇÃO
# =============================================================================

@router.post("/{market_id}/inutilizacoes", tags=["fiscal"])
async def create_inutilizacao(
    request: Request,
    market_id: uuid.UUID,
    cnpj: str = Form(...),
    document_type: str = Form("nfce"),
    series: int = Form(...),
    start_number: int = Form(...),
    end_number: int = Form(...),
    justification: str = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    audit: AuditService = Depends(get_audit_service),
    market=Depends(require_market_access(MarketPermission.FISCAL_WRITE)),
):
    """
    Inutiliza intervalo de numeração NFC-e junto à SEFAZ.

    Restrições:
    - Requer permissão FISCAL_WRITE.
    - Justificativa mínima de 15 caracteres.
    - Máximo de 1000 números por operação.
    - Idempotente: mesmo intervalo retorna registro existente se já autorizado.
    """
    from infra.repositories.fiscal_repo import (
        SQLAlchemyFiscalInutilizacaoRepository,
        SQLAlchemyFiscalTenantConfigRepository,
    )
    from infra.providers.fiscal.provider_factory import get_fiscal_provider
    from infra.config.settings import get_settings
    from application.services.fiscal.fiscal_inutilizacao_service import FiscalInutilizacaoService
    from domain.fiscal import FiscalEnvironment

    settings = get_settings()
    config_repo = SQLAlchemyFiscalTenantConfigRepository(db)
    cfg = await config_repo.get_by_market(market_id)
    if not cfg:
        raise HTTPException(400, "Configuração fiscal não encontrada para este mercado.")

    provider = get_fiscal_provider(settings.FISCAL_PROVIDER, cfg.environment.value)
    repo = SQLAlchemyFiscalInutilizacaoRepository(db)
    svc = FiscalInutilizacaoService(inutilizacao_repo=repo, provider=provider)

    try:
        entity = await svc.request_inutilizacao(
            market_id=market_id,
            requested_by_id=current_user.id,
            cnpj=cnpj,
            document_type=document_type,
            series=series,
            start_number=start_number,
            end_number=end_number,
            justification=justification,
            provider_name=settings.FISCAL_PROVIDER,
            environment=cfg.environment,
            api_token="",
        )
    except Exception as e:
        from domain.shared import BusinessRuleException
        if isinstance(e, BusinessRuleException):
            raise HTTPException(400, str(e))
        raise

    await record_audit_event(
        audit, request, actor=current_user, action="fiscal.inutilizacao.requested",
        resource_type="fiscal_inutilizacao", resource_id=str(entity.id),
        result="success" if entity.status == "authorized" else "pending",
        market_id=market_id,
        metadata={"series": series, "range": f"{start_number}-{end_number}", "status": entity.status},
    )

    return {
        "id": str(entity.id),
        "status": entity.status,
        "protocol": entity.protocol,
        "message": entity.provider_message,
        "error": entity.error_message,
        "series": entity.series,
        "start_number": entity.start_number,
        "end_number": entity.end_number,
    }


@router.get("/{market_id}/inutilizacoes", tags=["fiscal"])
async def list_inutilizacoes(
    market_id: uuid.UUID,
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    market=Depends(require_market_access(MarketPermission.FISCAL_READ)),
):
    """Lista inutilizações de numeração do mercado."""
    from infra.repositories.fiscal_repo import SQLAlchemyFiscalInutilizacaoRepository
    repo = SQLAlchemyFiscalInutilizacaoRepository(db)
    items, total = await repo.list_by_market(market_id, status=status, limit=limit, offset=offset)
    return {
        "total": total,
        "items": [
            {
                "id": str(i.id),
                "status": i.status,
                "document_type": i.document_type,
                "series": i.series,
                "start_number": i.start_number,
                "end_number": i.end_number,
                "justification": i.justification,
                "protocol": i.protocol,
                "authorized_at": i.authorized_at.isoformat() if i.authorized_at else None,
                "created_at": i.created_at.isoformat() if hasattr(i, "created_at") and i.created_at else None,
            }
            for i in items
        ],
    }


# =============================================================================
# QUOTA / BILLING
# =============================================================================

@router.get("/{market_id}/quota", tags=["fiscal"])
async def get_quota(
    market_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    market=Depends(require_market_access(MarketPermission.FISCAL_READ)),
):
    """Retorna consumo de cota do mês atual (owner-scoped — todos os mercados compartilham o pool)."""
    from infra.repositories.fiscal_repo import SQLAlchemyFiscalUsageRepository
    from application.services.fiscal.fiscal_quota_service import FiscalQuotaService
    from datetime import datetime
    repo = SQLAlchemyFiscalUsageRepository(db)
    quota_svc = FiscalQuotaService(repo)
    period = f"{datetime.utcnow().year}{datetime.utcnow().month:02d}"
    status = await quota_svc.get_quota_status(current_user.id, period)
    return {
        "period": status.period,
        "included_limit": status.included_limit,
        "addon_limit": status.addon_limit,
        "used": status.used_count,
        "reserved": status.reserved_count,
        "available": status.remaining,
        "usage_pct": status.percentage_used,
    }


# =============================================================================
# ONBOARDING FISCAL
# =============================================================================

@router.get("/{market_id}/onboarding", tags=["fiscal"])
async def get_onboarding_status(
    market_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    market=Depends(require_market_access(MarketPermission.FISCAL_READ)),
):
    """
    Retorna o checklist de onboarding fiscal com o estado atual de cada etapa.
    Indica o que falta para ativar a produção fiscal.
    """
    from infra.repositories.fiscal_repo import (
        SQLAlchemyFiscalDocumentRepository,
        SQLAlchemyFiscalTenantConfigRepository,
        SQLAlchemyProductTaxProfileRepository,
    )
    from application.services.fiscal.fiscal_onboarding_service import FiscalOnboardingService

    svc = FiscalOnboardingService(
        config_repo=SQLAlchemyFiscalTenantConfigRepository(db),
        doc_repo=SQLAlchemyFiscalDocumentRepository(db),
        tax_profile_repo=SQLAlchemyProductTaxProfileRepository(db),
    )
    status = await svc.get_onboarding_status(market_id)

    return {
        "market_id": str(status.market_id),
        "overall_status": status.overall_status,
        "completion_pct": status.completion_pct,
        "can_activate_production": status.can_activate_production,
        "missing_required": status.missing_required,
        "checklist": [
            {
                "key": c.key,
                "label": c.label,
                "description": c.description,
                "status": c.status,
                "details": c.details,
                "required_for_production": c.required_for_production,
            }
            for c in status.checklist
        ],
    }


# =============================================================================
# NEECTIFY FISCAL — ONBOARDING
# =============================================================================

@router.post("/{market_id}/neectify/sync-issuer", tags=["fiscal"])
async def neectify_sync_issuer(
    request: Request,
    market_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    audit: AuditService = Depends(get_audit_service),
    market=Depends(require_market_access(MarketPermission.FISCAL_WRITE)),
):
    """
    Cria ou atualiza o emitente no Neectify Fiscal com os dados do mercado.

    Atualiza neectify_issuer_id e neectify_onboarding_step = 'issuer_created'.
    """
    from infra.repositories.fiscal_repo import (
        SQLAlchemyFiscalTenantConfigRepository,
        SQLAlchemyProductTaxProfileRepository,
        SQLAlchemyFiscalDocumentRepository,
    )
    from infra.providers.fiscal.provider_factory import get_fiscal_provider
    from infra.config.settings import get_settings
    from application.services.fiscal.fiscal_onboarding_service import FiscalOnboardingService

    settings = get_settings()
    config_repo = SQLAlchemyFiscalTenantConfigRepository(db)

    if settings.FISCAL_PROVIDER != "neectify_fiscal":
        raise HTTPException(400, "Endpoint disponível apenas com FISCAL_PROVIDER=neectify_fiscal.")

    provider = get_fiscal_provider(settings.FISCAL_PROVIDER)

    from infra.security.secret_cipher import SecretCipher
    cipher = SecretCipher(settings.FISCAL_SECRET_KEY or settings.SECRET_KEY)

    svc = FiscalOnboardingService(
        config_repo=config_repo,
        doc_repo=SQLAlchemyFiscalDocumentRepository(db),
        tax_profile_repo=SQLAlchemyProductTaxProfileRepository(db),
        neectify_provider=provider,
        cipher=cipher,
    )

    market_data = {"trade_name": getattr(market, "name", None)}

    try:
        result = await svc.sync_issuer(market_id=market_id, market_data=market_data)
        # Passo 2 do onboarding: criar a config NFC-e (CSC/série) no Fiscal.
        # Sem isso, a emissão falha com 422 nfce.config_not_found. Idempotente:
        # pula se o CSC ainda não foi configurado ou se já existe config.
        config_result = await svc.sync_config(market_id=market_id)
        result["config_id"] = config_result.get("config_id")
        if config_result.get("skipped"):
            result["config_skipped"] = config_result["skipped"]

        # Passo 3: registrar o webhook de status (push) no Neectify — best-effort e
        # idempotente. Dá atualização quase-instantânea de status; sem ele o Marketfy
        # depende apenas do polling (reconcile). Falha aqui NÃO quebra o onboarding.
        callback_base = (settings.PUBLIC_API_BASE_URL or "").rstrip("/")
        if callback_base:
            try:
                wh = await svc.register_neectify_webhook(
                    market_id=market_id,
                    callback_url=f"{callback_base}/api/v1/fiscal/webhooks/neectify",
                )
                result["webhook_id"] = wh.get("webhook_id")
            except Exception as exc:
                logger.warning(
                    "neectify_register_webhook_skipped",
                    extra={"extra_data": {"market_id": str(market_id), "error": str(exc)}},
                )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except FiscalValidationError as exc:
        err = exc.details.get("error", {})
        detail = err.get("message") or str(exc)
        raise HTTPException(
            status_code=422,
            detail={
                "error": err.get("code", "issuer.validation_error"),
                "message": detail,
                "details": err.get("details", {}),
            },
        )
    except FiscalAuthError as exc:
        logger.error(
            "neectify_sync_issuer_auth_error",
            extra={"extra_data": {"market_id": str(market_id), "error": str(exc)}},
        )
        raise HTTPException(503, "Falha de autenticação com o Neectify Fiscal. Verifique a API key.")

    await record_audit_event(
        audit, request, actor=current_user, action="fiscal.neectify.sync_issuer",
        resource_type="fiscal_tenant_config", resource_id=str(market_id),
        result="success", market_id=market_id,
        metadata={"issuer_id": result.get("issuer_id")},
    )
    return result


@router.post("/{market_id}/neectify/sync-cert", tags=["fiscal"])
async def neectify_sync_cert(
    request: Request,
    market_id: uuid.UUID,
    environment: str = Form("homologation"),
    certificate_password: str = Form(...),
    certificate_file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    audit: AuditService = Depends(get_audit_service),
    market=Depends(require_market_access(MarketPermission.FISCAL_WRITE)),
):
    """
    Faz upload e ativa o certificado A1 no Neectify Fiscal.

    Atualiza neectify_cert_id e neectify_onboarding_step = 'cert_active'.
    A senha do certificado trafega backend-to-backend e nunca é logada.
    """
    from infra.repositories.fiscal_repo import (
        SQLAlchemyFiscalTenantConfigRepository,
        SQLAlchemyProductTaxProfileRepository,
        SQLAlchemyFiscalDocumentRepository,
    )
    from infra.providers.fiscal.provider_factory import get_fiscal_provider
    from infra.config.settings import get_settings
    from application.services.fiscal.fiscal_onboarding_service import FiscalOnboardingService

    settings = get_settings()

    if settings.FISCAL_PROVIDER != "neectify_fiscal":
        raise HTTPException(400, "Endpoint disponível apenas com FISCAL_PROVIDER=neectify_fiscal.")

    pfx_bytes = await certificate_file.read()
    validate_pfx_upload(
        certificate_file.filename or "",
        certificate_file.content_type,
        len(pfx_bytes),
        market_id,
    )

    provider = get_fiscal_provider(settings.FISCAL_PROVIDER)

    from infra.security.secret_cipher import SecretCipher
    cipher = SecretCipher(settings.FISCAL_SECRET_KEY or settings.SECRET_KEY)

    svc = FiscalOnboardingService(
        config_repo=SQLAlchemyFiscalTenantConfigRepository(db),
        doc_repo=SQLAlchemyFiscalDocumentRepository(db),
        tax_profile_repo=SQLAlchemyProductTaxProfileRepository(db),
        neectify_provider=provider,
        cipher=cipher,
    )

    try:
        result = await svc.sync_cert(
            market_id=market_id,
            pfx_bytes=pfx_bytes,
            certificate_password=certificate_password,
            environment=environment,
        )
        # Safety-net: garante a config NFC-e no Fiscal caso o CSC tenha sido
        # definido após o sync-issuer. Idempotente (pula se já existe).
        config_result = await svc.sync_config(market_id=market_id)
        result["config_id"] = config_result.get("config_id")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except FiscalValidationError as exc:
        err = exc.details.get("error", {})
        detail = err.get("message") or str(exc)
        raise HTTPException(
            status_code=422,
            detail={
                "error": err.get("code", "certificate.validation_error"),
                "message": detail,
                "details": err.get("details", {}),
            },
        )
    except FiscalAuthError as exc:
        logger.error("neectify_sync_cert_auth_error", extra={"extra_data": {"market_id": str(market_id), "error": str(exc)}})
        raise HTTPException(503, "Falha de autenticação com o Neectify Fiscal. Verifique a API key.")
    except Exception as exc:
        import httpx
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            logger.error(
                "neectify_sync_cert_http_error",
                extra={"extra_data": {"market_id": str(market_id), "http_status": status}},
            )
            if status == 404:
                raise HTTPException(422, "Emitente não encontrado no Neectify Fiscal. Execute sync-issuer novamente.")
            raise HTTPException(502, f"Neectify Fiscal retornou HTTP {status}.")
        logger.exception("neectify_sync_cert_unexpected_error", extra={"extra_data": {"market_id": str(market_id)}})
        raise HTTPException(500, "Erro inesperado ao enviar certificado.")

    await record_audit_event(
        audit, request, actor=current_user, action="fiscal.neectify.sync_cert",
        resource_type="fiscal_tenant_config", resource_id=str(market_id),
        result="success", market_id=market_id,
        metadata={"cert_id": result.get("cert_id"), "environment": environment},
    )
    return result


@router.get("/{market_id}/neectify/status", tags=["fiscal"])
async def neectify_status(
    market_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    market=Depends(require_market_access(MarketPermission.FISCAL_READ)),
):
    """Retorna neectify_onboarding_step e IDs de issuer/cert/config."""
    from infra.repositories.fiscal_repo import (
        SQLAlchemyFiscalTenantConfigRepository,
        SQLAlchemyProductTaxProfileRepository,
        SQLAlchemyFiscalDocumentRepository,
    )
    from application.services.fiscal.fiscal_onboarding_service import FiscalOnboardingService

    svc = FiscalOnboardingService(
        config_repo=SQLAlchemyFiscalTenantConfigRepository(db),
        doc_repo=SQLAlchemyFiscalDocumentRepository(db),
        tax_profile_repo=SQLAlchemyProductTaxProfileRepository(db),
    )
    return await svc.get_neectify_status(market_id)


# =============================================================================
# DOWNLOAD DE ARTEFATOS
# =============================================================================

@router.get("/artifacts/download", tags=["fiscal"])
async def download_artifact(
    key: str,
    exp: int,
    sig: str,
    current_user: User = Depends(get_current_user),
):
    """
    Download de artefato fiscal via URL assinada.
    A URL é gerada por get_signed_url() e expira em minutos.
    """
    from infra.storage.fiscal_artifact_storage import fiscal_artifact_storage
    if not fiscal_artifact_storage.validate_signed_url(key, exp, sig):
        raise HTTPException(403, "URL expirada ou inválida.")
    content = await fiscal_artifact_storage.load(key)
    if not content:
        raise HTTPException(404, "Artefato não encontrado.")
    content_type = "application/pdf" if key.endswith(".pdf") else "application/xml"
    return Response(content=content, media_type=content_type)


# =============================================================================
# HELPER
# =============================================================================

def _doc_to_dict(doc) -> dict:
    return {
        "id": str(doc.id),
        "sale_id": str(doc.sale_id),
        "document_type": doc.document_type,
        "provider": doc.provider,
        "provider_ref": doc.provider_ref,
        "environment": doc.environment.value,
        "status": doc.status.value,
        "series": doc.series,
        "number": doc.number,
        "access_key": doc.access_key,
        "protocol": doc.protocol,
        "sefaz_status_code": doc.sefaz_status_code,
        "sefaz_message": doc.sefaz_message,
        "authorized_at": doc.authorized_at.isoformat() if doc.authorized_at else None,
        "canceled_at": doc.canceled_at.isoformat() if doc.canceled_at else None,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "contingency_mode": doc.contingency_mode,
    }
