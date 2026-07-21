from __future__ import annotations
import json
from typing import AsyncGenerator, Optional

from infra.cache.redis_adapter import redis_client


def channel(attempt_id: str) -> str:
    return f"pix:attempt:{attempt_id}"


class PixEventBus:
    """Publica/assina mudanças de estado de tentativas Pix via Redis pub/sub.

    Reflete estado já persistido pelo backend — nunca é fonte de verdade.
    """

    def __init__(self, redis=None):
        self._redis = redis  # injetável (testes); senão usa redis_client.redis

    @property
    def redis(self):
        return self._redis if self._redis is not None else redis_client.redis

    async def publish(self, attempt_id: str, event: str, data: dict) -> None:
        payload = json.dumps({"event": event, "data": data})
        await self.redis.publish(channel(attempt_id), payload)

    async def subscribe(self, attempt_id: str) -> AsyncGenerator[Optional[dict], None]:
        """Yields `{event, data}` dicts as they're published. Yields `None` on each
        idle poll (no message within 1s) so a consumer can drive its own heartbeat
        cadence without needing to cancel/timeout this generator from the outside —
        cancelling an in-flight `__anext__()` would tear down the pub/sub connection
        via this method's own `finally` block, making the generator unusable after
        the first heartbeat. Looping with a plain `async for` avoids that entirely.
        """
        ps = self.redis.pubsub()
        await ps.subscribe(channel(attempt_id))
        try:
            while True:
                msg = await ps.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg.get("type") == "message":
                    raw = msg["data"]
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    yield json.loads(raw)
                else:
                    yield None
        finally:
            await ps.unsubscribe(channel(attempt_id))
            await ps.close()
