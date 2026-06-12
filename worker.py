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
    ]
