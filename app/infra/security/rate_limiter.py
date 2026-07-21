import time
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import HTTPException, Request, status
from redis.exceptions import RedisError

from infra.cache.redis_adapter import redis_client
from infra.config.settings import get_settings


class InMemoryRateLimiter:
    def __init__(self):
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)

    def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        window_start = now - window_seconds
        hits = self._hits[key]

        while hits and hits[0] <= window_start:
            hits.popleft()

        if len(hits) >= limit:
            return False

        hits.append(now)
        return True


rate_limiter = InMemoryRateLimiter()


def get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def enforce_rate_limit(request: Request, bucket: str, limit: int, window_seconds: int) -> None:
    client_ip = get_client_ip(request)
    key = f"{bucket}:{client_ip}"
    if not rate_limiter.hit(key, limit=limit, window_seconds=window_seconds):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas tentativas. Tente novamente em instantes.",
        )


async def enforce_rate_limit_async(request: Request, bucket: str, limit: int, window_seconds: int) -> None:
    settings = get_settings()
    client_ip = get_client_ip(request)
    key = f"rate_limit:{bucket}:{client_ip}"

    if settings.RATE_LIMIT_BACKEND != "redis":
        enforce_rate_limit(request, bucket=bucket, limit=limit, window_seconds=window_seconds)
        return

    try:
        count = await redis_client.redis.incr(key)
        if count == 1:
            await redis_client.redis.expire(key, window_seconds)
    except RedisError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Controle de taxa indisponivel.",
        )

    if count > limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas tentativas. Tente novamente em instantes.",
        )


async def enforce_pix_verify_rate_limit(request, *, attempt_id, sale_id, user_id, market_id,
                                        cooldown_seconds: int) -> None:
    settings = get_settings()
    checks = [
        (f"pix_verify:attempt:{attempt_id}", 1, cooldown_seconds),
        (f"pix_verify:user:{user_id}", 20, 60),
        (f"pix_verify:market:{market_id}", 120, 60),
    ]
    for bucket, limit, window in checks:
        # reusa o backend configurado (memória/redis) por chave dedicada (não por IP)
        key = f"rate_limit:{bucket}"
        if settings.RATE_LIMIT_BACKEND != "redis":
            if not rate_limiter.hit(key, limit=limit, window_seconds=window):
                raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                                    detail="Aguarde antes de verificar novamente.")
        else:
            try:
                count = await redis_client.redis.incr(key)
                if count == 1:
                    await redis_client.redis.expire(key, window)
            except RedisError:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                    detail="Controle de taxa indisponivel.")
            if count > limit:
                raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                                    detail="Aguarde antes de verificar novamente.")
