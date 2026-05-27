"""
fiscal_jobs.py — Jobs ARQ para processamento fiscal assíncrono.

Cada job é idempotente: se chamado duas vezes para o mesmo doc_id, não duplica.
Todos os jobs leem a configuração descriptografada diretamente do banco,
nunca de parâmetros externos (segurança).

Jobs disponíveis:
- emit_nfce_job         — emite NFC-e chamando o provider
- reconcile_nfce_job    — consulta provider para resolver estados incertos
- download_artifacts_job — baixa XML/PDF após autorização
- check_certificates_job — alertas de vencimento de certificado
- notify_quota_job       — checa e envia alertas de cota
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from infra.config.logger import get_logger

logger = get_logger("fiscal_jobs")


# =============================================================================
# EMIT JOB
# =============================================================================

async def emit_nfce_job(
    ctx: dict,
    doc_id: str,
    market_id: str,
    owner_id: str,
    period: str = "",
    consuming_addon: bool = False,
) -> dict:
    """
    Executa a emissão de NFC-e fora da request HTTP do caixa.

    Idempotente: documentos já autorizados ou em estado terminal são ignorados.
    Backoff: erros recuperáveis agendam retry via reconcile_nfce_job.
    """
    from sqlalchemy.ext.asyncio import AsyncSession
    from infra.database.setup import async_session_factory
    from infra.repositories.fiscal_repo import (
        SQLAlchemyFiscalAttemptRepository,
        SQLAlchemyFiscalDocumentRepository,
        SQLAlchemyFiscalEventRepository,
        SQLAlchemyFiscalTenantConfigRepository,
        SQLAlchemyFiscalUsageRepository,
    )
    from infra.providers.fiscal.provider_factory import get_fiscal_provider
    from infra.security.secret_cipher import SecretCipher
    from infra.config.settings import get_settings
    from application.services.fiscal.fiscal_pre_validator import FiscalPreValidator
    from application.services.fiscal.fiscal_quota_service import FiscalQuotaService
    from domain.fiscal import (
        FiscalAttempt, FiscalAttemptOperation, FiscalAttemptStatus,
        FiscalDocumentStatus, FiscalEvent, FiscalEventSource,
    )
    from infra.providers.fiscal.base import EmitResultStatus

    doc_uuid = uuid.UUID(doc_id)
    market_uuid = uuid.UUID(market_id)
    owner_uuid = uuid.UUID(owner_id)
    settings = get_settings()

    logger.info("emit_nfce_job_start",
                extra={"extra_data": {"doc_id": doc_id, "market_id": market_id}})

    async with async_session_factory() as session:
        doc_repo = SQLAlchemyFiscalDocumentRepository(session)
        attempt_repo = SQLAlchemyFiscalAttemptRepository(session)
        event_repo = SQLAlchemyFiscalEventRepository(session)
        config_repo = SQLAlchemyFiscalTenantConfigRepository(session)
        usage_repo = SQLAlchemyFiscalUsageRepository(session)

        doc = await doc_repo.get_by_id(doc_uuid)
        if not doc:
            logger.error("emit_job_doc_not_found", extra={"extra_data": {"doc_id": doc_id}})
            return {"status": "not_found"}

        if doc.is_terminal():
            logger.info("emit_job_already_terminal",
                        extra={"extra_data": {"doc_id": doc_id, "status": doc.status.value}})
            return {"status": doc.status.value}

        # Obter configuração descriptografada
        cfg_raw = await config_repo.get_by_market(market_uuid)
        if not cfg_raw:
            doc.set_manual_action_required("Configuração fiscal não encontrada.")
            await doc_repo.save(doc)
            return {"status": "config_missing"}

        # Para Neectify, api_token não é usado diretamente (client gerencia)
        # Mantemos a variável para compatibilidade com a interface do provider
        api_token = settings.NEECTIFY_API_KEY or ""

        # Obter venda para montar payload
        from infra.repositories.sqlalchemy_repos import SQLAlchemySaleRepository
        sale_repo = SQLAlchemySaleRepository(session)
        sale = await sale_repo.get_by_id(doc.sale_id)
        if not sale:
            doc.set_manual_action_required("Venda não encontrada.")
            await doc_repo.save(doc)
            return {"status": "sale_not_found"}

        # Montar payload fiscal no formato Neectify
        validator = FiscalPreValidator()
        provider_name = settings.FISCAL_PROVIDER

        if provider_name == "neectify_fiscal":
            issuer_id = getattr(cfg_raw, "neectify_issuer_id", None) or ""
            payload = validator.build_neectify_payload(
                sale=sale,
                fiscal_config=cfg_raw,
                issuer_id=issuer_id,
            )
        else:
            payload = validator.build_fiscal_payload(
                sale=sale,
                fiscal_config=cfg_raw,
                provider_ref=doc.provider_ref,
            )

        # Registrar tentativa
        attempt_count = await attempt_repo.count_attempts(doc_uuid, "emit")
        attempt = FiscalAttempt(
            fiscal_document_id=doc_uuid,
            market_id=market_uuid,
            provider=doc.provider,
            operation=FiscalAttemptOperation.EMIT,
            attempt_number=attempt_count + 1,
            started_at=datetime.utcnow(),
        )

        doc.status = FiscalDocumentStatus.PROCESSING
        await doc_repo.save(doc)

        # Chamar provider
        provider = get_fiscal_provider(provider_name, cfg_raw.environment.value)

        result = await provider.emit_nfce(
            provider_ref=doc.provider_ref,
            payload=payload,
            api_token=api_token,
            environment=cfg_raw.environment.value,
        )

        attempt.finish(
            status=FiscalAttemptStatus.SUCCESS if result.is_authorized else FiscalAttemptStatus.FAILED,
            http_status=result.http_status,
            error_code=result.error_code,
            error_message=result.error_message,
            provider_request_id=result.provider_request_id,
        )
        await attempt_repo.save(attempt)
        doc.last_attempt_id = attempt.id

        quota_service = FiscalQuotaService(usage_repo)
        emit_period = period or datetime.utcnow().strftime("%Y%m")

        if result.is_authorized:
            doc.set_authorized(
                access_key=result.access_key or "",
                protocol=result.protocol or "",
                number=result.number or 0,
                series=result.series or cfg_raw.nfce_series,
                sefaz_code=result.sefaz_status_code or "100",
                sefaz_msg=result.sefaz_message or "Autorizado",
            )
            await doc_repo.save(doc)
            await quota_service.consume(
                owner_id=owner_uuid,
                market_id=market_uuid,
                period=emit_period,
                consuming_addon=consuming_addon,
                sale_id=doc.sale_id,
                fiscal_document_id=doc_uuid,
            )
            await _append_event(event_repo, doc, "authorized",
                                 f"Autorizado: {(result.access_key or '')[:20]}...",
                                 FiscalEventSource.PROVIDER)

            # Enfileirar download de artefatos (baixa prioridade — não bloqueia o caixa)
            if "arq" in ctx:
                await ctx["arq"].enqueue_job(
                    "download_artifacts_job",
                    doc_id=doc_id, market_id=market_id,
                    _queue_name="fiscal:reconcile",
                    _defer_by=timedelta(seconds=2),
                )

            logger.info("emit_nfce_authorized",
                        extra={"extra_data": {
                            "doc_id": doc_id,
                            "access_key": (result.access_key or "")[:8],
                            "duration_ms": attempt.duration_ms,
                        }})
            return {"status": "authorized", "access_key": result.access_key}

        elif result.is_rejected:
            doc.set_rejected(
                sefaz_code=result.error_code or "",
                sefaz_msg=result.error_message or "Rejeitado",
            )
            await doc_repo.save(doc)
            await quota_service.release(
                owner_id=owner_uuid,
                period=emit_period,
                market_id=market_uuid,
                reason="rejected",
            )
            await _append_event(event_repo, doc, "rejected",
                                 f"Rejeitado {result.error_code}: {result.error_message}",
                                 FiscalEventSource.PROVIDER)
            logger.warning("emit_nfce_rejected",
                           extra={"extra_data": {"doc_id": doc_id, "code": result.error_code}})
            return {"status": "rejected", "error": result.error_message}

        elif result.is_canceled:
            doc.set_rejected(
                sefaz_code="",
                sefaz_msg="NFC-e cancelada antes da autorização.",
            )
            await doc_repo.save(doc)
            await _append_event(event_repo, doc, "canceled",
                                 "NFC-e cancelada (retorno do provider).",
                                 FiscalEventSource.PROVIDER)
            return {"status": "canceled"}

        elif result.needs_reconciliation:
            # Timeout ou ref duplicada — reconciliar antes de reenviar
            doc.status = FiscalDocumentStatus.PROVIDER_ERROR
            await doc_repo.save(doc)
            await _append_event(event_repo, doc, "needs_reconciliation",
                                 f"Estado incerto: {result.status.value}",
                                 FiscalEventSource.WORKER)
            if "arq" in ctx:
                await ctx["arq"].enqueue_job(
                    "reconcile_nfce_job",
                    doc_id=doc_id, market_id=market_id, owner_id=owner_id,
                    _queue_name="fiscal:reconcile",
                    _defer_by=timedelta(seconds=30),
                )
            return {"status": "pending_reconciliation"}

        else:
            # Erro recuperável — agendar reconciliação com backoff
            doc.status = FiscalDocumentStatus.PROVIDER_ERROR
            await doc_repo.save(doc)
            await _append_event(event_repo, doc, "provider_error",
                                 result.error_message or "Erro provider",
                                 FiscalEventSource.PROVIDER)
            if result.is_recoverable and "arq" in ctx:
                await ctx["arq"].enqueue_job(
                    "reconcile_nfce_job",
                    doc_id=doc_id, market_id=market_id, owner_id=owner_id,
                    _queue_name="fiscal:reconcile",
                    _defer_by=timedelta(seconds=60),
                )
            return {"status": "provider_error", "recoverable": result.is_recoverable}


# =============================================================================
# RECONCILE JOB
# =============================================================================

async def reconcile_nfce_job(
    ctx: dict, doc_id: str, market_id: str, owner_id: str
) -> dict:
    """Consulta o provider para resolver estado incerto de um documento."""
    from sqlalchemy.ext.asyncio import AsyncSession
    from infra.database.setup import async_session_factory
    from infra.repositories.fiscal_repo import (
        SQLAlchemyFiscalAttemptRepository,
        SQLAlchemyFiscalDocumentRepository,
        SQLAlchemyFiscalEventRepository,
        SQLAlchemyFiscalTenantConfigRepository,
    )
    from infra.providers.fiscal.provider_factory import get_fiscal_provider
    from infra.security.secret_cipher import SecretCipher
    from infra.config.settings import get_settings
    from application.services.fiscal.fiscal_reconciliation_service import FiscalReconciliationService

    settings = get_settings()
    doc_uuid = uuid.UUID(doc_id)
    market_uuid = uuid.UUID(market_id)

    async with async_session_factory() as session:
        doc_repo = SQLAlchemyFiscalDocumentRepository(session)
        attempt_repo = SQLAlchemyFiscalAttemptRepository(session)
        event_repo = SQLAlchemyFiscalEventRepository(session)
        config_repo = SQLAlchemyFiscalTenantConfigRepository(session)

        cfg = await config_repo.get_by_market(market_uuid)
        if not cfg:
            return {"status": "config_missing"}

        provider_name = settings.FISCAL_PROVIDER
        if provider_name == "neectify_fiscal":
            api_token = settings.NEECTIFY_API_KEY or ""
        else:
            cipher = SecretCipher(settings.FISCAL_SECRET_KEY or settings.SECRET_KEY)
            api_token = cipher.decrypt(cfg.csc_token_ciphertext or "") or \
                        getattr(settings, "FOCUS_NFE_API_TOKEN", "")

        provider = get_fiscal_provider(provider_name, cfg.environment.value)

        svc = FiscalReconciliationService(doc_repo, attempt_repo, event_repo, provider)
        doc = await svc.reconcile_document(doc_uuid, api_token)

        return {"status": doc.status.value}


# =============================================================================
# DOWNLOAD ARTIFACTS JOB
# =============================================================================

async def download_artifacts_job(ctx: dict, doc_id: str, market_id: str) -> dict:
    """Baixa XML e PDF após autorização e armazena em storage privado."""
    from infra.database.setup import async_session_factory
    from infra.repositories.fiscal_repo import (
        SQLAlchemyFiscalArtifactRepository,
        SQLAlchemyFiscalDocumentRepository,
        SQLAlchemyFiscalTenantConfigRepository,
    )
    from infra.providers.fiscal.provider_factory import get_fiscal_provider
    from infra.security.secret_cipher import SecretCipher
    from infra.config.settings import get_settings
    from infra.storage.fiscal_artifact_storage import FiscalArtifactStorage
    from application.services.fiscal.fiscal_artifact_service import FiscalArtifactService

    settings = get_settings()
    doc_uuid = uuid.UUID(doc_id)
    market_uuid = uuid.UUID(market_id)

    async with async_session_factory() as session:
        doc_repo = SQLAlchemyFiscalDocumentRepository(session)
        artifact_repo = SQLAlchemyFiscalArtifactRepository(session)
        config_repo = SQLAlchemyFiscalTenantConfigRepository(session)

        doc = await doc_repo.get_by_id(doc_uuid)
        if not doc or doc.status.value != "authorized":
            return {"skipped": True}

        cfg = await config_repo.get_by_market(market_uuid)
        if not cfg:
            return {"error": "config_missing"}

        provider_name = settings.FISCAL_PROVIDER
        if provider_name == "neectify_fiscal":
            api_token = settings.NEECTIFY_API_KEY or ""
        else:
            cipher = SecretCipher(settings.FISCAL_SECRET_KEY or settings.SECRET_KEY)
            api_token = cipher.decrypt(cfg.csc_token_ciphertext or "") or \
                        getattr(settings, "FOCUS_NFE_API_TOKEN", "")

        provider = get_fiscal_provider(provider_name, cfg.environment.value)
        storage = FiscalArtifactStorage()
        svc = FiscalArtifactService(artifact_repo, storage, provider)

        results = await svc.download_and_store(doc, api_token)
        return results


# =============================================================================
# CHECK CERTIFICATES JOB (periódico — fiscal:maintenance)
# =============================================================================

async def check_certificates_job(ctx: dict) -> dict:
    """Job diário que verifica certificados próximos do vencimento."""
    from infra.database.setup import async_session_factory
    from infra.repositories.fiscal_repo import (
        SQLAlchemyFiscalNotificationRepository,
        SQLAlchemyFiscalTenantConfigRepository,
    )
    from application.services.fiscal.fiscal_notification_service import FiscalNotificationService
    from sqlalchemy import select
    from infra.database.models import FiscalTenantConfigModel, MarketModel
    from datetime import timedelta

    alerted = 0
    async with async_session_factory() as session:
        # Buscar todos os configs com certificado próximo do vencimento
        from infra.database.models import FiscalTenantConfigModel
        from sqlalchemy import select, and_
        now = datetime.utcnow()
        threshold = now + timedelta(days=30)

        q = select(FiscalTenantConfigModel).where(
            FiscalTenantConfigModel.enabled == True,
            FiscalTenantConfigModel.certificate_valid_until <= threshold,
            FiscalTenantConfigModel.certificate_valid_until >= now,
        )
        r = await session.execute(q)
        configs = r.scalars().all()

        notif_repo = SQLAlchemyFiscalNotificationRepository(session)
        notif_svc = FiscalNotificationService(notif_repo)

        for cfg_m in configs:
            # Determinar owner do mercado
            from infra.database.models import MarketModel
            market = await session.get(MarketModel, cfg_m.market_id)
            if not market:
                continue

            from infra.repositories.fiscal_repo import _to_tenant_config
            cfg = _to_tenant_config(cfg_m)
            await notif_svc.notify_certificate_expiring(
                owner_id=market.owner_id,
                market_id=market.id,
                cfg=cfg,
            )
            alerted += 1

    logger.info("check_certificates_done", extra={"extra_data": {"alerted": alerted}})
    return {"alerted": alerted}


# =============================================================================
# NOTIFY QUOTA JOB (periódico — fiscal:notifications)
# =============================================================================

async def notify_quota_job(ctx: dict) -> dict:
    """Job periódico que verifica cotas e envia alertas."""
    from infra.database.setup import async_session_factory
    from infra.repositories.fiscal_repo import (
        SQLAlchemyFiscalNotificationRepository,
        SQLAlchemyFiscalUsageRepository,
    )
    from application.services.fiscal.fiscal_notification_service import FiscalNotificationService
    from infra.database.models import FiscalUsageCounterModel
    from sqlalchemy import select, and_

    now = datetime.utcnow()
    period = f"{now.year}{now.month:02d}"
    notified = 0

    async with async_session_factory() as session:
        q = select(FiscalUsageCounterModel).where(
            FiscalUsageCounterModel.period_yyyymm == period
        )
        r = await session.execute(q)
        counters = r.scalars().all()

        notif_repo = SQLAlchemyFiscalNotificationRepository(session)
        usage_repo = SQLAlchemyFiscalUsageRepository(session)
        notif_svc = FiscalNotificationService(notif_repo)

        for ctr in counters:
            total = ctr.included_limit + ctr.addon_limit
            if total == 0:
                continue
            pct = (ctr.reserved_count / total) * 100
            if pct >= 80:
                await notif_svc.notify_quota_usage(
                    owner_id=ctr.owner_id,
                    market_id=ctr.market_id,
                    period=period,
                    usage_pct=pct,
                )
                notified += 1

    return {"notified": notified}


# =============================================================================
# RESET MONTHLY FISCAL QUOTAS JOB (cron — 1º dia do mês, 00:05 UTC)
# =============================================================================

async def reset_monthly_fiscal_quotas(ctx: dict) -> dict:
    """
    Reseta contadores mensais de cota fiscal para todos os owners com fiscal ativo.

    Executado no 1º dia de cada mês às 00:05 UTC via ARQ cron.
    Para cada owner:
    - Calcula o novo período (YYYYMM do mês atual).
    - Busca o limite incluído no plano do owner.
    - Busca a soma de créditos addon ainda válidos (carryover).
    - Faz upsert do counter para o novo período zerando used/reserved.
    """
    from infra.database.setup import async_session_factory
    from infra.repositories.fiscal_repo import SQLAlchemyFiscalUsageRepository
    from infra.repositories.sqlalchemy_repos import (
        SQLAlchemyUserRepository,
        SQLAlchemyPlanRepository,
    )
    from infra.repositories.billing_repo import SQLAlchemyBillingSubscriptionRepository
    from application.services.plan_access_service import PlanAccessService

    now = datetime.utcnow()
    new_period = f"{now.year}{now.month:02d}"

    processed = 0
    errors = 0

    async with async_session_factory() as session:
        usage_repo = SQLAlchemyFiscalUsageRepository(session)
        user_repo = SQLAlchemyUserRepository(session)
        plan_repo = SQLAlchemyPlanRepository(session)
        sub_repo = SQLAlchemyBillingSubscriptionRepository(session)
        plan_access = PlanAccessService(user_repo, plan_repo, sub_repo)

        owner_ids = await usage_repo.get_owners_with_active_fiscal_config()

        for owner_id in owner_ids:
            try:
                included_limit = await plan_access.get_fiscal_monthly_limit(owner_id)
                addon_carryover = await usage_repo.sum_active_packages_remaining(owner_id)

                await usage_repo.upsert_counter(
                    owner_id=owner_id,
                    period=new_period,
                    included_limit=included_limit,
                    addon_limit=addon_carryover,
                    used_count=0,
                    reserved_count=0,
                )
                processed += 1

                logger.info(
                    "fiscal_quota_reset",
                    extra={"extra_data": {
                        "owner_id": str(owner_id),
                        "period": new_period,
                        "included_limit": included_limit,
                        "addon_carryover": addon_carryover,
                    }},
                )
            except Exception as exc:
                errors += 1
                logger.error(
                    "fiscal_quota_reset_error",
                    extra={"extra_data": {"owner_id": str(owner_id), "error": str(exc)}},
                )

    logger.info(
        "reset_monthly_fiscal_quotas_done",
        extra={"extra_data": {"period": new_period, "processed": processed, "errors": errors}},
    )
    return {"period": new_period, "processed": processed, "errors": errors}


# =============================================================================
# RECONCILE PENDING BILLING CORE PAYMENTS JOB
# =============================================================================

async def reconcile_pending_bc_payments(
    ctx: dict,
    credits_repo=None,
    bc_client=None,
    fiscal_credits_service=None,
) -> dict:
    """
    Consulta o Billing Core para pacotes com bc_payment_id definido
    mas ainda em status 'pending', criados há mais de N minutos.
    Respeita o rate limit (HTTP 429 / BillingCoreRateLimitError) interrompendo o lote.
    """
    checked = 0
    activated = 0
    failed = 0
    errors = 0

    if credits_repo and bc_client and fiscal_credits_service:
        from infra.clients.billing_core_client import BillingCoreRateLimitError
        from infra.config.settings import get_settings

        settings = get_settings()
        cutoff = datetime.utcnow() - timedelta(minutes=settings.RECONCILE_PENDING_MINUTES)
        packages = await credits_repo.get_pending_packages_with_payment_id_older_than(cutoff)

        for package in packages:
            checked += 1
            try:
                payment = await bc_client.get_payment(package.bc_payment_id)
                status = (payment.get("payment_status") or "").upper()
                if status in ("CONFIRMED", "RECEIVED", "RECEIVED_IN_CASH"):
                    await fiscal_credits_service.activate_package(
                        package_id=package.id,
                        bc_payment_id=payment["payment_id"],
                        payment_data=payment,
                    )
                    activated += 1
                elif status in ("OVERDUE", "REFUNDED"):
                    await fiscal_credits_service.mark_package_failed(package.id, reason=f"Status: {status}")
                    failed += 1
            except BillingCoreRateLimitError:
                logger.warning("bc_reconcile_rate_limited", extra={"extra_data": {"package_id": str(package.id)}})
                break
            except Exception as e:
                errors += 1
                logger.error("bc_reconcile_error", extra={"extra_data": {"package_id": str(package.id), "error": str(e)}})

        return {"checked": checked, "activated": activated, "failed": failed, "errors": errors}

    from infra.config.settings import get_settings
    from infra.database.setup import async_session_factory
    from infra.clients.billing_core_client import BillingCoreClient
    from infra.repositories.audit_repo import SQLAlchemyAuditLogRepository
    from infra.repositories.fiscal_repo import (
        SQLAlchemyFiscalNotificationRepository,
        SQLAlchemyFiscalUsageRepository,
    )
    from application.services.audit_service import AuditService
    from application.services.fiscal.fiscal_credits_service import FiscalCreditsService
    from application.services.fiscal.fiscal_notification_service import FiscalNotificationService
    from application.services.fiscal.fiscal_quota_service import FiscalQuotaService
    from infra.repositories.sqlalchemy_repos import SQLAlchemyUserRepository

    settings = get_settings()
    async with async_session_factory() as session:
        repo = SQLAlchemyFiscalUsageRepository(session)
        user_repo = SQLAlchemyUserRepository(session)
        client = BillingCoreClient()
        quota_service = FiscalQuotaService(repo)
        service = FiscalCreditsService(
            credits_repo=repo,
            quota_repo=repo,
            mp_client=None,
            bc_client=client,
            user_repo=user_repo,
            quota_service=quota_service,
            notification_service=FiscalNotificationService(SQLAlchemyFiscalNotificationRepository(session)),
            audit_service=AuditService(SQLAlchemyAuditLogRepository(session)),
            settings=settings,
        )
        return await reconcile_pending_bc_payments(
            ctx,
            credits_repo=repo,
            bc_client=client,
            fiscal_credits_service=service,
        )


# =============================================================================
# HELPER
# =============================================================================

async def _append_event(event_repo, doc, event_type, message, source):
    from domain.fiscal import FiscalEvent
    try:
        event = FiscalEvent(
            fiscal_document_id=doc.id,
            market_id=doc.market_id,
            event_type=event_type,
            source=source,
            message=message,
        )
        await event_repo.append(event)
    except Exception as exc:
        logger.warning("event_append_failed", extra={"extra_data": {"error": str(exc)}})
