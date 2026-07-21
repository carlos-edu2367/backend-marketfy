# ruff: noqa: E402
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import json
import pytest


class FakePubSub:
    def __init__(self, messages):
        self._messages = list(messages)
        self.subscribed = None
        self.unsubscribed = None
        self.closed = False

    async def subscribe(self, channel):
        self.subscribed = channel

    async def unsubscribe(self, channel):
        self.unsubscribed = channel

    async def get_message(self, ignore_subscribe_messages=True, timeout=None):
        if self._messages:
            return {"type": "message", "data": self._messages.pop(0)}
        return None

    async def close(self):
        self.closed = True


class FakeRedis:
    def __init__(self, messages):
        self.published = []
        self._ps = FakePubSub(messages)

    async def publish(self, channel, data):
        self.published.append((channel, data))

    def pubsub(self):
        return self._ps


@pytest.mark.asyncio
async def test_publish_formats_channel_and_payload():
    from infra.cache.pix_event_bus import PixEventBus

    redis = FakeRedis([])
    bus = PixEventBus(redis=redis)
    await bus.publish("att-1", "payment.approved", {"status": "approved"})
    channel, payload = redis.published[0]
    assert channel == "pix:attempt:att-1"
    assert json.loads(payload) == {"event": "payment.approved", "data": {"status": "approved"}}


@pytest.mark.asyncio
async def test_subscribe_yields_events():
    from infra.cache.pix_event_bus import PixEventBus

    msg = json.dumps({"event": "payment.approved", "data": {"status": "approved"}})
    redis = FakeRedis([msg])
    bus = PixEventBus(redis=redis)
    gen = bus.subscribe("att-1")
    evt = await gen.__anext__()
    assert evt["event"] == "payment.approved"
    assert evt["data"] == {"status": "approved"}
    await gen.aclose()


def test_channel_helper_matches_publish_and_subscribe():
    from infra.cache.pix_event_bus import channel

    assert channel("att-1") == "pix:attempt:att-1"


@pytest.mark.asyncio
async def test_subscribe_yields_none_on_idle_poll():
    """Consumers (SSE endpoint) drive heartbeat cadence off these idle ticks —
    the generator must not require external cancellation/timeout to keep going,
    since that would tear down the pub/sub connection via its own cleanup."""
    from infra.cache.pix_event_bus import PixEventBus

    redis = FakeRedis([])  # nunca há mensagem: toda chamada a get_message expira
    bus = PixEventBus(redis=redis)
    gen = bus.subscribe("att-1")
    tick = await gen.__anext__()
    assert tick is None
    await gen.aclose()
