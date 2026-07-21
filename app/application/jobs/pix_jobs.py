"""
pix_jobs.py — Jobs ARQ para conciliação assíncrona de pagamentos Pix.

O fluxo em tempo real (webhook + polling do PDV) resolve a maioria das
tentativas rapidamente. Estes jobs cobrem os casos residuais: tentativas
cuja última consulta de status ficou velha (reconciliação periódica),
tentativas cujo QR já venceu (expiração), e tokens OAuth do Mercado Pago
perto de vencer (renovação proativa).

`PixReconciler` é a lógica testável isoladamente (repos/serviço injetáveis).
Os wrappers `*_job(ctx)` são a cola ARQ: abrem uma sessão via
`infra.database.setup.async_session_factory` (mesmo padrão de
`application/jobs/fiscal_jobs.py`) e delegam para a lógica de negócio.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from infra.config.logger import get_logger

logger = get_logger("pix_jobs")


class PixReconciler:
    """Reverifica tentativas Pix ativas com repos/serviço injetáveis (testável isoladamente)."""

    def __init__(self, attempt_repo, payment_service, stale_minutes: int = 2):
        self.attempt_repo = attempt_repo
        self.payment_service = payment_service
        self.stale_minutes = stale_minutes

    async def reconcile(self, now: datetime) -> dict:
        older_than = now - timedelta(minutes=self.stale_minutes)
        attempts = await self.attempt_repo.list_active_stale(older_than, limit=50)
        processed = 0
        for a in attempts:
            try:
                await self.payment_service.verify(
                    market_id=a.market_id, attempt_id=a.id, source="reconciliation")
                processed += 1
            except Exception:
                logger.warning("pix_reconcile_attempt_failed", extra={"extra_data": {"attempt": str(a.id)[:6]}})
        return {"processed": processed, "scanned": len(attempts)}


# =============================================================================
# ARQ JOB WRAPPERS
# =============================================================================

async def reconcile_pending_attempts(ctx) -> dict:
    """Job periódico: reverifica tentativas Pix ativas com consulta de status velha."""
    from infra.database.setup import async_session_factory
    from infra.repositories.pix_repo import PixPaymentAttemptRepository
    from infra.web.dependencies import get_pix_payment_service

    async with async_session_factory() as session:
        attempt_repo = PixPaymentAttemptRepository(session)
        payment_service = get_pix_payment_service(session)
        reconciler = PixReconciler(attempt_repo, payment_service)
        result = await reconciler.reconcile(now=datetime.now(timezone.utc))

    logger.info("pix_reconcile_pending_done", extra={"extra_data": result})
    return result


async def expire_overdue_attempts(ctx) -> dict:
    """Job periódico: reverifica tentativas Pix ativas cujo QR já venceu (expires_at < now).

    Reusa `PixPaymentService.verify`, que já resolve o caso "provider informa
    expirado" (Plan 2) — aqui só garantimos que essas tentativas não fiquem
    presas em `pending`/`in_analysis` esperando o próximo webhook/polling.
    """
    from infra.database.setup import async_session_factory
    from infra.repositories.pix_repo import PixPaymentAttemptRepository
    from infra.web.dependencies import get_pix_payment_service

    now = datetime.now(timezone.utc)
    processed = 0
    scanned = 0

    async with async_session_factory() as session:
        attempt_repo = PixPaymentAttemptRepository(session)
        payment_service = get_pix_payment_service(session)

        attempts = await attempt_repo.list_active_expired(before=now, limit=50)
        scanned = len(attempts)
        for a in attempts:
            try:
                await payment_service.verify(
                    market_id=a.market_id, attempt_id=a.id, source="reconciliation")
                processed += 1
            except Exception:
                logger.warning(
                    "pix_expire_attempt_failed", extra={"extra_data": {"attempt": str(a.id)[:6]}})

    result = {"processed": processed, "scanned": scanned}
    logger.info("pix_expire_overdue_done", extra={"extra_data": result})
    return result


async def refresh_expiring_tokens(ctx) -> dict:
    """Job periódico: renova proativamente tokens OAuth do Mercado Pago perto de vencer.

    Escopo: reusa `MercadoPagoConnectionService.get_valid_access_token`, que já
    contém toda a lógica de renovação com lock distribuído (Plan 1) — este job
    apenas descobre quais conexões estão perto do vencimento
    (`MercadoPagoConnectionRepository.list_expiring`, novo método simples) e
    dispara a renovação para cada uma, evitando que a primeira requisição do
    caixa depois de um período ocioso pague o custo do refresh síncrono.
    Não existe (nem foi necessário criar) um método de "refresh em lote" dedicado;
    chamar `get_valid_access_token` por conexão é suficiente e reaproveita a
    lógica já testada.
    """
    from infra.database.setup import async_session_factory
    from infra.repositories.pix_repo import MercadoPagoConnectionRepository, MercadoPagoPosRegistrationRepository
    from application.services.pix.connection_service import (
        MercadoPagoConnectionService, ConnectionNotReadyError, ReauthorizationRequiredError,
    )
    from infra.clients.mercadopago_client import MercadoPagoClient
    from infra.cache.redis_lock import RedisLock

    now = datetime.now(timezone.utc)
    threshold = now + timedelta(hours=24)
    checked = 0
    refreshed = 0

    async with async_session_factory() as session:
        conn_repo = MercadoPagoConnectionRepository(session)
        conn_service = MercadoPagoConnectionService(
            conn_repo, MercadoPagoClient(), RedisLock(),
            pos_repo=MercadoPagoPosRegistrationRepository(session),
        )
        connections = await conn_repo.list_expiring(threshold, limit=200)
        for conn in connections:
            checked += 1
            try:
                await conn_service.get_valid_access_token(conn.market_id)
                refreshed += 1
            except (ConnectionNotReadyError, ReauthorizationRequiredError) as exc:
                logger.warning(
                    "pix_token_refresh_needs_reauth",
                    extra={"extra_data": {"market_id": str(conn.market_id), "error": str(exc)}},
                )
            except Exception:
                logger.warning(
                    "pix_token_refresh_failed",
                    extra={"extra_data": {"market_id": str(conn.market_id)}},
                )

    result = {"checked": checked, "refreshed": refreshed}
    logger.info("pix_refresh_expiring_tokens_done", extra={"extra_data": result})
    return result
