import asyncio
import json
from typing import Optional, Any
from redis.asyncio import Redis, from_url
from infra.config.settings import get_settings

settings = get_settings()

class RedisAdapter:
    def __init__(self):
        self._redis: Optional[Redis] = None
        self._redis_loop: Optional[asyncio.AbstractEventLoop] = None
        self.default_ttl = 3600 # 1 hora

    @property
    def redis(self) -> Redis:
        """Cria (ou recria) o client sob demanda, amarrado ao event loop atual.

        `redis.asyncio.Redis` liga suas conexões pooladas ao loop em que a
        primeira operação rodou. Um singleton criado uma única vez no import
        (antes de qualquer loop existir) quebra ao ser reusado por um loop
        diferente — cenário raro em produção (um único loop pela vida do
        processo), mas real em runners de teste com um loop novo por teste
        (pytest-asyncio, modo function-scoped): a conexão presa ao loop
        anterior, já fechado, falha com 'Event loop is closed'.
        """
        loop = asyncio.get_event_loop()
        if self._redis is None or self._redis_loop is not loop:
            self._redis = from_url(settings.REDIS_URL, decode_responses=True)
            self._redis_loop = loop
        return self._redis

    async def close(self):
        await self.redis.close()

    async def get(self, key: str) -> Optional[dict]:
        """Recupera e desserializa um objeto JSON."""
        data = await self.redis.get(key)
        if data:
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                return None
        return None

    async def set(self, key: str, value: Any, ttl: int = None):
        """Serializa e salva um objeto."""
        expiration = ttl if ttl is not None else self.default_ttl
        # Serializer simples. Para objetos complexos (Dates/UUIDs), 
        # precisaríamos de um encoder customizado.
        # Aqui assumimos que 'value' é um dict ou primitivo serializável.
        json_data = json.dumps(value, default=str) 
        await self.redis.set(key, json_data, ex=expiration)

    async def delete(self, key: str):
        await self.redis.delete(key)
    
    async def delete_pattern(self, pattern: str):
        """Deleta chaves em lote (cuidado em prod)."""
        keys = await self.redis.keys(pattern)
        if keys:
            await self.redis.delete(*keys)

# Singleton global para reutilização de conexão
redis_client = RedisAdapter()

async def get_redis_client():
    try:
        yield redis_client
    finally:
        # Em aplicações serverless ou testes, fecharíamos aqui.
        # No FastAPI persistente, mantemos aberto.
        pass