"""
ARQ worker configuration.

Filas fiscais:
- fiscal:high    — emissão imediata disparada pelo PDV
- fiscal:retry   — retries com backoff exponencial
- fiscal:reconcile — consulta de notas em estado incerto
- fiscal:notifications — notificações e alertas
- fiscal:maintenance   — jobs periódicos (cert vencendo, fechamento mensal)

ARQ usa Redis como broker. Compatível com FastAPI async.
"""
from __future__ import annotations

import os
from typing import Any, Dict

from infra.config.settings import get_settings

QUEUE_FISCAL_HIGH = "fiscal:high"
QUEUE_FISCAL_RETRY = "fiscal:high"  # Remapeado para a fila única fiscal:high para que o único worker processe
QUEUE_FISCAL_RECONCILE = "fiscal:high"  # Remapeado para a fila única fiscal:high para que o único worker processe
QUEUE_FISCAL_NOTIFICATIONS = "fiscal:high"  # Remapeado para a fila única fiscal:high para que o único worker processe
QUEUE_FISCAL_MAINTENANCE = "fiscal:high"  # Remapeado para a fila única fiscal:high para que o único worker processe

QUEUE_PIX_HIGH = "pix:high"
QUEUE_PIX_RECONCILE = "pix:high"  # Remapeado para a fila única pix:high

ALL_QUEUES = [
    QUEUE_FISCAL_HIGH,
    QUEUE_FISCAL_RETRY,
    QUEUE_FISCAL_RECONCILE,
    QUEUE_FISCAL_NOTIFICATIONS,
    QUEUE_FISCAL_MAINTENANCE,
    QUEUE_PIX_HIGH,
]


def get_redis_settings():
    """Retorna configurações Redis para o ARQ worker."""
    try:
        from arq.connections import RedisSettings
    except ImportError:
        raise ImportError("Pacote 'arq' não instalado. Execute: pip install arq")

    settings = get_settings()
    redis_url = settings.REDIS_URL

    if redis_url.startswith("redis://") or redis_url.startswith("rediss://"):
        from urllib.parse import urlparse, unquote
        parsed = urlparse(redis_url)
        
        host = parsed.hostname or "localhost"
        port = parsed.port if parsed.port is not None else 6379
        
        database = 0
        if parsed.path:
            try:
                database = int(parsed.path.lstrip("/"))
            except ValueError:
                pass
                
        username = unquote(parsed.username) if parsed.username else None
        password = unquote(parsed.password) if parsed.password else None

        return RedisSettings(
            host=host,
            port=port,
            database=database,
            username=username,
            password=password,
            ssl=parsed.scheme == "rediss",
            ssl_cert_reqs="none" if parsed.scheme == "rediss" else None
        )

    return RedisSettings()


async def get_arq_pool():
    """Retorna pool de conexão ARQ para enfileirar jobs."""
    try:
        from arq import create_pool
    except ImportError:
        raise ImportError("Pacote 'arq' não instalado. Execute: pip install arq")
    return await create_pool(get_redis_settings())


class WorkerSettings:
    """
    Configurações do ARQ worker fiscal.
    Importado pelo entry point `worker.py`.
    """
    # Importações lazy das funções de job (evita circular imports no startup)
    functions: list = []  # Preenchido no worker.py

    redis_settings = None  # Preenchido no worker.py

    # Número de corrotinas paralelas
    max_jobs = int(os.environ.get("FISCAL_WORKER_CONCURRENCY", "5"))

    # Timeout por job (segundos)
    job_timeout = int(os.environ.get("FISCAL_JOB_TIMEOUT", "30"))

    # Retry máximo padrão
    max_tries = 5

    # Intervalo de polling
    poll_delay = 0.5

    # Health check
    health_check_interval = 60

    on_startup = None   # Preenchido no worker.py
    on_shutdown = None  # Preenchido no worker.py

    queue_name = QUEUE_FISCAL_HIGH
