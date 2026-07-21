"""
Fiscal Worker — entry point para o ARQ worker.

Execução:
    python -m arq worker.WorkerSettings

Variáveis de ambiente necessárias:
    REDIS_URL         — URL do Redis (default: redis://localhost:6379/0)
    DATABASE_URL      — URL do PostgreSQL
    FISCAL_SECRET_KEY — Chave de criptografia fiscal
    FISCAL_PROVIDER   — Provider: focus_nfe | fake (default: focus_nfe)
    FOCUS_NFE_API_TOKEN — Token da Focus NFe (quando FISCAL_PROVIDER=focus_nfe)
"""
from __future__ import annotations

import sys
import os

# Adicionar app ao path para imports relativos
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

from application.jobs.fiscal_jobs import (
    check_certificates_job,
    download_artifacts_job,
    emit_nfce_job,
    notify_quota_job,
    reconcile_pending_bc_payments,
    reconcile_nfce_job,
    reset_monthly_fiscal_quotas,
)
from application.jobs.billing_jobs import (
    generate_due_invoices,
    reconcile_pending_invoices,
)
from application.jobs.pix_jobs import (
    reconcile_pending_attempts,
    expire_overdue_attempts,
    refresh_expiring_tokens,
    scan_pix_anomalies,
)
from arq.cron import cron
from infra.queues.arq_config import get_redis_settings, ALL_QUEUES
from infra.config.logger import get_logger

logger = get_logger("worker")


async def startup(ctx: dict):
    """Inicializa recursos compartilhados do worker."""
    from infra.database.setup import init_db
    logger.info("fiscal_worker_startup")
    try:
        await init_db()
    except Exception as exc:
        logger.warning("init_db_failed", extra={"extra_data": {"error": str(exc)}})


async def shutdown(ctx: dict):
    """Fecha recursos ao encerrar o worker."""
    logger.info("fiscal_worker_shutdown")
    try:
        from infra.providers.fiscal.provider_factory import _singleton_neectify
        if _singleton_neectify:
            await _singleton_neectify.close()
    except Exception:
        pass


class WorkerSettings:
    """Configurações do worker ARQ — importado por `python -m arq worker.WorkerSettings`."""

    functions = [
        emit_nfce_job,
        reconcile_nfce_job,
        download_artifacts_job,
        check_certificates_job,
        notify_quota_job,
        reset_monthly_fiscal_quotas,
        reconcile_pending_bc_payments,
        generate_due_invoices,
        reconcile_pending_invoices,
        reconcile_pending_attempts,
        expire_overdue_attempts,
        refresh_expiring_tokens,
        scan_pix_anomalies,
    ]

    redis_settings = get_redis_settings()

    queue_name = "fiscal:high"

    max_jobs = int(os.environ.get("FISCAL_WORKER_CONCURRENCY", "5"))
    job_timeout = int(os.environ.get("FISCAL_JOB_TIMEOUT", "30"))
    max_tries = 3
    poll_delay = 0.5
    health_check_interval = 60

    on_startup = startup
    on_shutdown = shutdown

    # Cron jobs periódicos
    cron_jobs = [
        # Verificação de certificados — todo dia às 09:00
        # cron(check_certificates_job, hour=9, minute=0),
        # Notificações de cota — a cada hora
        # cron(notify_quota_job, minute=0),
        # Reset mensal de cotas — 1º dia do mês às 00:05 UTC
        cron(reset_monthly_fiscal_quotas, day=1, hour=0, minute=5),
        cron(reconcile_pending_bc_payments, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
        cron(generate_due_invoices, hour=8, minute=0),               # diário 08:00 UTC
        cron(reconcile_pending_invoices, minute={2, 12, 22, 32, 42, 52}),
        # Pix: reverifica tentativas com consulta de status velha — a cada ~2 min
        cron(reconcile_pending_attempts, minute=set(range(0, 60, 2))),
        # Pix: reverifica tentativas cujo QR já venceu — a cada ~2 min (mesmo ritmo da reconciliação)
        cron(expire_overdue_attempts, minute=set(range(1, 60, 2))),
        # Pix: renovação proativa de tokens OAuth perto de vencer — diário 03:00 UTC
        cron(refresh_expiring_tokens, hour=3, minute=0),
        # Pix: detector de anomalias críticas (pago-sem-venda / venda-sem-confirmação) — a cada ~5 min
        cron(scan_pix_anomalies, minute=set(range(0, 60, 5))),
    ]
