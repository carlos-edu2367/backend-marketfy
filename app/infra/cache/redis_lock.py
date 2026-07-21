import uuid

from infra.cache.redis_adapter import redis_client


class RedisLock:
    """Lock distribuído simples baseado em SET NX EX / DEL condicional (via token)."""

    def __init__(self):
        self._tokens: dict[str, str] = {}

    async def acquire(self, key: str, ttl: int) -> bool:
        token = str(uuid.uuid4())
        acquired = await redis_client.redis.set(key, token, nx=True, ex=ttl)
        if acquired:
            self._tokens[key] = token
            return True
        return False

    async def release(self, key: str) -> None:
        token = self._tokens.pop(key, None)
        if token is None:
            return
        current = await redis_client.redis.get(key)
        if current == token:
            await redis_client.redis.delete(key)
