from __future__ import annotations

from typing import Awaitable, Callable, Optional

from sqlalchemy import text

from infra.config.settings import get_settings
from infra.cache.redis_adapter import redis_client
from infra.database.setup import AsyncSessionLocal


async def database_ready_check() -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(text("SELECT 1"))


async def redis_ready_check() -> None:
    await redis_client.redis.ping()


async def readiness_payload(
    database_check: Optional[Callable[[], Awaitable[None]]] = None,
    redis_check: Optional[Callable[[], Awaitable[None]]] = None,
) -> tuple[dict, int]:
    check = database_check or database_ready_check
    redis = redis_check or redis_ready_check
    checks = {}
    status_code = 200

    try:
        await check()
        checks["database"] = {"status": "ok"}
    except Exception:
        checks["database"] = {"status": "unavailable"}
        status_code = 503

    if get_settings().RATE_LIMIT_BACKEND == "redis":
        try:
            await redis()
            checks["redis"] = {"status": "ok"}
        except Exception:
            checks["redis"] = {"status": "unavailable"}
            status_code = 503

    return {
        "status": "ready" if status_code == 200 else "not_ready",
        "checks": checks,
    }, status_code
