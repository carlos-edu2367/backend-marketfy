"""Router de billing — Fase 4 / PRs 14 e 15.

Endpoints:
  GET  /billing/subscription         — status consolidado da assinatura
  POST /billing/subscriptions        — iniciar assinatura via Billing Core
  GET  /billing/jobs/{job_id}        — polling de job (proxy)
  GET  /billing/plans/features       — features e limites do plano atual
  POST /billing/webhooks/internal    — webhook idempotente do Billing Core

Segurança:
  - API key e URL do Billing Core nunca retornados ao frontend.
  - Webhook valida X-Webhook-Signature-256 (HMAC-SHA256/base64, JSON canônico).
  - Toda validação de plano ocorre no backend.
  - Fallback seguro quando Billing Core indisponível.
"""

import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from application.dtos import (
    BillingSubscriptionResponseDTO,
    BillingWebhookEventDTO,
    InitiateSubscriptionRequestDTO,
    BillingJobStatusResponseDTO,
    SubscribeRequestDTO,
)
from application.services.subscription_service import SubscriptionService
from application.services.audit_service import AuditService
from application.services.plan_access_service import PlanAccessService, SubscriptionStatus
from domain.shared import BusinessRuleException
from infra.config.logger import get_logger
from infra.config.settings import get_settings
from infra.database.setup import get_db
from infra.web.dependencies import get_current_user, get_subscription_service
from infra.web.dependencies import get_audit_service
from infra.clients.billing_core_client import BillingCoreClient, BillingCoreError
from infra.observability.audit import record_audit_event
from infra.observability.metrics import metrics_registry

router = APIRouter()
logger = get_logger("billing_router")
settings = get_settings()


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def _get_subscription_service(db: AsyncSession = Depends(get_db)) -> SubscriptionService:
    from infra.repositories.sqlalchemy_repos import SQLAlchemyUserRepository, SQLAlchemyPlanRepository
    from infra.repositories.billing_repo import SQLAlchemyBillingSubscriptionRepository, SQLAlchemyBillingEventRepository

    return SubscriptionService(
        user_repo=SQLAlchemyUserRepository(db),
        plan_repo=SQLAlchemyPlanRepository(db),
        subscription_repo=SQLAlchemyBillingSubscriptionRepository(db),
        event_repo=SQLAlchemyBillingEventRepository(db),
        billing_client=BillingCoreClient(),
    )


def _get_plan_access_service(db: AsyncSession = Depends(get_db)) -> PlanAccessService:
    from infra.repositories.sqlalchemy_repos import SQLAlchemyUserRepository, SQLAlchemyPlanRepository
    from infra.repositories.billing_repo import SQLAlchemyBillingSubscriptionRepository

    return PlanAccessService(
        user_repo=SQLAlchemyUserRepository(db),
        plan_repo=SQLAlchemyPlanRepository(db),
        subscription_repo=SQLAlchemyBillingSubscriptionRepository(db),
    )


def _get_invoice_service(db: AsyncSession = Depends(get_db)):
    from application.services.invoice_service import InvoiceService
    from infra.repositories.billing_invoice_repo import SQLAlchemyBillingInvoiceRepository
    from infra.repositories.billing_repo import SQLAlchemyBillingSubscriptionRepository
    from infra.repositories.sqlalchemy_repos import SQLAlchemyPlanRepository

    return InvoiceService(
        invoice_repo=SQLAlchemyBillingInvoiceRepository(db),
        subscription_repo=SQLAlchemyBillingSubscriptionRepository(db),
        plan_repo=SQLAlchemyPlanRepository(db),
        billing_client=BillingCoreClient(),
        settings=settings,
    )


@router.post("/subscribe", status_code=status.HTTP_202_ACCEPTED)
async def subscribe(
    request: Request,
    dto: SubscribeRequestDTO,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    invoice_service=Depends(_get_invoice_service),
    audit: AuditService = Depends(get_audit_service),
):
    if dto.subscription_type not in ("monthly", "semiannual", "annual"):
        raise HTTPException(status_code=400, detail="subscription_type inválido.")
    if dto.billing_mode not in ("invoice", "recurring"):
        raise HTTPException(status_code=400, detail="billing_mode inválido.")

    idem = dto.idempotency_key or f"sub-{current_user.id}-{dto.plan_id}-{dto.subscription_type}-{dto.billing_mode}"

    if dto.billing_mode == "invoice":
        try:
            result = await invoice_service.contract(
                owner_id=current_user.id, plan_id=dto.plan_id,
                subscription_type=dto.subscription_type, idempotency_key=idem,
            )
            await db.commit()
        except BusinessRuleException as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except BillingCoreError as exc:
            logger.warning(f"[billing] Billing Core indisponível subscribe user={current_user.id}: {exc}")
            raise HTTPException(status_code=503, detail="Serviço de cobrança temporariamente indisponível.")
        await record_audit_event(
            audit, request, actor=current_user, action="billing.subscribe.invoice",
            resource_type="billing_subscription", resource_id=result.get("subscription_id"),
            result="success", metadata={"plan_id": str(dto.plan_id), "subscription_type": dto.subscription_type},
        )
        return result

    # recurring
    if not dto.document:
        raise HTTPException(status_code=400, detail="Documento (CPF/CNPJ) é obrigatório para cobrança recorrente.")
    from application.services.recurring_service import RecurringService
    from infra.repositories.billing_repo import SQLAlchemyBillingSubscriptionRepository
    from infra.repositories.sqlalchemy_repos import SQLAlchemyPlanRepository, SQLAlchemyUserRepository
    rec = RecurringService(
        subscription_repo=SQLAlchemyBillingSubscriptionRepository(db),
        plan_repo=SQLAlchemyPlanRepository(db),
        user_repo=SQLAlchemyUserRepository(db),
        billing_client=BillingCoreClient(),
        settings=settings,
    )
    try:
        result = await rec.contract(
            user=current_user, plan_id=dto.plan_id,
            subscription_type=dto.subscription_type, document=dto.document, idempotency_key=idem,
        )
        await db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except BillingCoreError as exc:
        logger.warning(f"[billing] Billing Core indisponível recurring user={current_user.id}: {exc}")
        raise HTTPException(status_code=503, detail="Serviço de cobrança temporariamente indisponível.")
    await record_audit_event(
        audit, request, actor=current_user, action="billing.subscribe.recurring",
        resource_type="billing_subscription", resource_id=result.get("subscription_id"),
        result="success", metadata={"plan_id": str(dto.plan_id), "subscription_type": dto.subscription_type},
    )
    return result


@router.get("/invoices")
async def list_invoices(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from infra.repositories.billing_invoice_repo import SQLAlchemyBillingInvoiceRepository
    repo = SQLAlchemyBillingInvoiceRepository(db)
    items = await repo.list_by_owner(current_user.id, limit=50, offset=0)
    return {
        "items": [
            {
                "invoice_id": str(i.id),
                "amount": str(i.amount),
                "status": i.status,
                "period_start": i.period_start.isoformat() if i.period_start else None,
                "period_end": i.period_end.isoformat() if i.period_end else None,
                "due_date": i.due_date.isoformat() if i.due_date else None,
                "checkout_url": i.checkout_url,
                "paid_at": i.paid_at.isoformat() if i.paid_at else None,
                "created_at": i.created_at.isoformat() if i.created_at else None,
            }
            for i in items
        ]
    }


@router.get("/invoices/{invoice_id}")
async def get_invoice(
    invoice_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    invoice_service=Depends(_get_invoice_service),
):
    from infra.repositories.billing_invoice_repo import SQLAlchemyBillingInvoiceRepository
    repo = SQLAlchemyBillingInvoiceRepository(db)
    inv = await repo.get_by_id(invoice_id)
    if inv is None or inv.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Fatura não encontrada.")
    checkout = await invoice_service.refresh_checkout(invoice_id)
    await db.commit()
    return {
        "invoice_id": str(inv.id),
        "amount": str(inv.amount),
        "status": inv.status,
        "due_date": inv.due_date.isoformat() if inv.due_date else None,
        "checkout_url": checkout.get("checkout_url") or inv.checkout_url,
    }


# ---------------------------------------------------------------------------
# GET /billing/subscription
# ---------------------------------------------------------------------------

@router.get("/subscription", response_model=BillingSubscriptionResponseDTO)
async def get_subscription_status(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: PlanAccessService = Depends(_get_plan_access_service),
):
    """Retorna status consolidado da assinatura do usuário autenticado.

    O frontend usa este endpoint para exibir banners, bloquear features
    e informar o usuário sobre o estado da sua assinatura.
    Nunca expõe API key ou dados internos do Billing Core.
    """
    from infra.repositories.billing_invoice_repo import SQLAlchemyBillingInvoiceRepository
    try:
        features_dict = await service.get_plan_features(current_user.id)

        sub_result = await service.get_subscription_status(current_user.id)
        is_pending = sub_result.subscription_status == SubscriptionStatus.PENDING

        pending_invoice = None
        invoice_pending = False
        if sub_result.billing_mode == "invoice":
            inv_repo = SQLAlchemyBillingInvoiceRepository(db)
            inv = await inv_repo.get_latest_pending_by_owner(current_user.id)
            if inv is not None:
                invoice_pending = True
                pending_invoice = {
                    "invoice_id": str(inv.id),
                    "amount": str(inv.amount),
                    "due_date": inv.due_date.isoformat() if inv.due_date else None,
                    "checkout_url": inv.checkout_url,
                    "status": inv.status,
                }

        return BillingSubscriptionResponseDTO(
            status=sub_result.subscription_status,
            plan_name=sub_result.plan_name,
            expires_at=sub_result.expires_at,
            billing_pending=is_pending,
            billing_mode=sub_result.billing_mode,
            locked=sub_result.locked,
            invoice_pending=invoice_pending,
            pending_invoice=pending_invoice,
            features=features_dict.get("features"),
            limits=features_dict.get("limits"),
        )
    except Exception as exc:
        logger.error(f"[billing] Erro ao buscar assinatura user={current_user.id}: {exc}")
        raise HTTPException(status_code=500, detail="Erro ao consultar assinatura.")


# ---------------------------------------------------------------------------
# POST /billing/subscriptions
# ---------------------------------------------------------------------------

@router.post("/subscriptions", status_code=status.HTTP_202_ACCEPTED)
async def initiate_subscription(
    request: Request,
    dto: InitiateSubscriptionRequestDTO,
    current_user=Depends(get_current_user),
    service: SubscriptionService = Depends(_get_subscription_service),
    audit: AuditService = Depends(get_audit_service),
):
    """Inicia uma nova assinatura via Billing Core.

    O frontend envia plan_id e subscription_type.
    O backend monta e assina o payload para o Billing Core.
    Retorna job_id para polling opcional.
    """
    if dto.subscription_type not in ("trial", "monthly", "semiannual", "annual"):
        raise HTTPException(
            status_code=400,
            detail="subscription_type inválido. Use: trial, monthly, semiannual ou annual.",
        )

    try:
        result = await service.initiate_subscription(
            owner_id=current_user.id,
            plan_id=dto.plan_id,
            subscription_type=dto.subscription_type,
            customer_provider_id=dto.customer_provider_id,
            idempotency_key=dto.idempotency_key,
        )
        await record_audit_event(
            audit,
            request,
            actor=current_user,
            action="billing.subscription.created",
            resource_type="billing_subscription",
            resource_id=result.get("subscription_id"),
            result="success",
            metadata={"plan_id": dto.plan_id, "subscription_type": dto.subscription_type},
        )
        return result
    except BusinessRuleException as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except BillingCoreError as exc:
        logger.warning(f"[billing] Billing Core indisponível na criação user={current_user.id}: {exc}")
        raise HTTPException(
            status_code=503,
            detail="Serviço de cobrança temporariamente indisponível. Tente novamente em instantes.",
        )
    except Exception as exc:
        logger.error(f"[billing] Erro inesperado na criação user={current_user.id}: {exc}")
        raise HTTPException(status_code=500, detail="Erro ao iniciar assinatura.")


# ---------------------------------------------------------------------------
# GET /billing/jobs/{job_id}
# ---------------------------------------------------------------------------

@router.get("/jobs/{job_id}", response_model=BillingJobStatusResponseDTO)
async def get_job_status(
    job_id: str,
    current_user=Depends(get_current_user),
):
    """Polling de status de job no Billing Core.

    O formato de resposta do Billing Core necessita validação manual.
    Este endpoint é um proxy seguro — nunca expõe a API key.
    """
    client = BillingCoreClient()
    try:
        result = await client.get_job_status(job_id)
        return BillingJobStatusResponseDTO(
            job_id=job_id,
            status=result.get("status"),
            result=result,
        )
    except BillingCoreError as exc:
        if exc.status_code == 404:
            raise HTTPException(status_code=404, detail=f"Job {job_id} não encontrado.")
        raise HTTPException(
            status_code=503,
            detail="Serviço de cobrança temporariamente indisponível.",
        )
    except Exception as exc:
        logger.error(f"[billing] Erro ao consultar job {job_id}: {exc}")
        raise HTTPException(status_code=500, detail="Erro ao consultar status do job.")


# ---------------------------------------------------------------------------
# GET /billing/plans/features
# ---------------------------------------------------------------------------

@router.get("/plans/features")
async def get_plan_features(
    current_user=Depends(get_current_user),
    service: PlanAccessService = Depends(_get_plan_access_service),
):
    """Retorna features e limites do plano atual do usuário.

    Usado pelo frontend para ajustar UI sem conhecer a lógica de plano.
    """
    try:
        return await service.get_plan_features(current_user.id)
    except Exception as exc:
        logger.error(f"[billing] Erro ao buscar features user={current_user.id}: {exc}")
        raise HTTPException(status_code=500, detail="Erro ao consultar features do plano.")


# ---------------------------------------------------------------------------
# POST /billing/webhooks/internal  (PR 15)
# ---------------------------------------------------------------------------

@router.post("/webhooks/internal", status_code=status.HTTP_200_OK)
async def receive_billing_webhook(
    request: Request,
    service: SubscriptionService = Depends(_get_subscription_service),
    audit: AuditService = Depends(get_audit_service),
):
    """Recebe eventos do Billing Core de forma idempotente.

    Validações obrigatórias:
      1. X-Webhook-Signature-256 — HMAC-SHA256 do JSON canônico em base64,
         assinado com BILLING_CORE_WEBHOOK_SECRET (= INTERNAL_WEBHOOK_SIGNATURE
         do Billing Core).
      2. event_id obrigatório para idempotência.

    O payload bruto é persistido antes do processamento para auditoria.
    Se o evento já foi processado (mesmo event_id), retorna sucesso idempotente.
    """
    # 1. Parsear payload
    try:
        raw_body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Payload inválido (JSON esperado).")

    # 2. Validar assinatura X-Webhook-Signature-256
    signature = request.headers.get("X-Webhook-Signature-256")
    expected_secret = settings.BILLING_CORE_WEBHOOK_SECRET
    if not signature or not expected_secret:
        logger.warning("[webhook] Assinatura ou segredo ausente no webhook do Billing Core.")
        raise HTTPException(status_code=401, detail="Assinatura inválida.")

    canonical_json = json.dumps(raw_body, sort_keys=True, separators=(",", ":"))
    expected_digest = hmac.new(
        expected_secret.encode("utf-8"),
        canonical_json.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    expected_b64 = base64.b64encode(expected_digest).decode("utf-8")
    if not hmac.compare_digest(expected_b64, signature):
        logger.warning("[webhook] Assinatura X-Webhook-Signature-256 inválida no webhook do Billing Core.")
        raise HTTPException(status_code=401, detail="Assinatura inválida.")

    try:
        payload = BillingWebhookEventDTO(**raw_body)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Payload de webhook inválido: {exc}")

    if not payload.event_id:
        raise HTTPException(status_code=400, detail="event_id obrigatório para idempotência.")

    # 4. Processar evento
    try:
        result = await service.process_webhook_event(
            event_id=payload.event_id,
            event_type=payload.event_type,
            system=payload.system,
            system_sub_id=payload.system_sub_id,
            billing_subscription_id=payload.subscription_id,
            status_from_event=payload.status,
            expires_at=payload.expires_at,
            raw_payload=raw_body,
        )
        metrics_registry.record_billing_webhook(payload.event_id, result.get("result"))
        await record_audit_event(
            audit,
            request,
            actor=None,
            action="billing.webhook.processed",
            resource_type="billing_event",
            resource_id=payload.event_id,
            result=result.get("result", "unknown"),
            metadata={"event_type": payload.event_type, "system": payload.system},
        )
        return result
    except BusinessRuleException as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(f"[webhook] Erro ao processar evento {payload.event_id}: {exc}")
        metrics_registry.record_billing_webhook(payload.event_id, "failed")
        await record_audit_event(
            audit,
            request,
            actor=None,
            action="billing.webhook.processed",
            resource_type="billing_event",
            resource_id=payload.event_id,
            result="failed",
            metadata={"event_type": payload.event_type, "system": payload.system},
        )
        # Retornar 200 para o Billing Core não re-tentar indefinidamente
        # O evento foi persistido para análise manual
        return {"result": "error_persisted", "event_id": payload.event_id}
