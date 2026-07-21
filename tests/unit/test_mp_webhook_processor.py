from __future__ import annotations

import os
import sys
import uuid

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)


class FakeWebhookRepo:
    def __init__(self): self.events = {}; self.mark_failed_calls = []
    async def get_event(self, provider, event_id): return self.events.get(event_id)
    async def create_event(self, **kw):
        class E: pass
        e = E(); e.id = uuid.uuid4(); e.processing_status = kw.get("processing_status", "received")
        self.events[kw["event_id"]] = e; return e
    async def mark_processed(self, eid):
        for e in self.events.values():
            if e.id == eid: e.processing_status = "processed"
    async def mark_failed(self, eid): self.mark_failed_calls.append(eid)


class Attempt:
    def __init__(self, market_id, order_id):
        self.id = uuid.uuid4(); self.market_id = market_id; self.order_id = order_id
        self.receiver_account_id = "42"


class FakeAttemptRepo:
    def __init__(self, attempt): self.attempt = attempt
    async def get_by_order_id(self, oid): return self.attempt if self.attempt.order_id == oid else None


class Conn:
    mp_user_id = "42"


class FakeConnRepo:
    async def get_by_market(self, market_id):
        return Conn()


class FakeService:
    def __init__(self): self.verified = None
    async def verify(self, *, market_id, attempt_id, source):
        self.verified = (market_id, attempt_id, source)
        class A: status = "approved"
        return A()


class FakeAttemptRepoRaises:
    """Raises once the webhook event already exists, to exercise the
    processor's exception-handling path (must still return 200 and attempt
    mark_failed rather than propagate)."""
    async def get_by_order_id(self, oid):
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_process_approved_triggers_verify(monkeypatch):
    monkeypatch.setenv("MP_WEBHOOK_SECRET", "s")
    from infra.config import settings as sm; sm.get_settings.cache_clear()
    from application.services.pix.webhook_processor import MercadoPagoWebhookProcessor
    market_id = uuid.uuid4()
    attempt = Attempt(market_id, "ord1")
    svc = FakeService()
    proc = MercadoPagoWebhookProcessor(FakeWebhookRepo(), FakeAttemptRepo(attempt),
                                       FakeConnRepo(), svc)
    payload = {"type": "order", "action": "order.processed",
               "data": {"id": "ORD1"}, "user_id": "42"}
    status = await proc.process(payload, b"{}", {"x-request-id": "r1"})
    assert status == 200
    assert svc.verified[1] == attempt.id  # verify chamado para a tentativa certa


@pytest.mark.asyncio
async def test_process_duplicate_is_noop(monkeypatch):
    monkeypatch.setenv("MP_WEBHOOK_SECRET", "s")
    from infra.config import settings as sm; sm.get_settings.cache_clear()
    from application.services.pix.webhook_processor import MercadoPagoWebhookProcessor
    market_id = uuid.uuid4()
    attempt = Attempt(market_id, "ord1")
    repo = FakeWebhookRepo()
    # pré-marca como processed — event_id usa data_id já normalizado (lowercase)
    e = await repo.create_event(provider="mercado_pago", event_id="ord1:order.processed",
                                processing_status="processed")
    svc = FakeService()
    proc = MercadoPagoWebhookProcessor(repo, FakeAttemptRepo(attempt), FakeConnRepo(), svc)
    payload = {"type": "order", "action": "order.processed", "data": {"id": "ORD1"}, "user_id": "42"}
    status = await proc.process(payload, b"{}", {})
    assert status == 200 and svc.verified is None  # não reprocessa


@pytest.mark.asyncio
async def test_process_data_id_case_insensitive_dedup_and_lookup(monkeypatch):
    # Mercado Pago's data.id casing isn't guaranteed to match exactly what
    # was used when the attempt/order was created; the processor must
    # normalize to lowercase both when building the dedup key and when
    # resolving the attempt by order_id, mirroring validate_mp_signature's
    # internal lowercasing (Task 2).
    monkeypatch.setenv("MP_WEBHOOK_SECRET", "s")
    from infra.config import settings as sm; sm.get_settings.cache_clear()
    from application.services.pix.webhook_processor import MercadoPagoWebhookProcessor
    market_id = uuid.uuid4()
    # attempt stored with lowercase order_id, as it would be normalized at creation
    attempt = Attempt(market_id, "ord1")
    repo = FakeWebhookRepo()
    svc = FakeService()
    proc = MercadoPagoWebhookProcessor(repo, FakeAttemptRepo(attempt), FakeConnRepo(), svc)

    # webhook arrives with mixed-case data.id
    payload = {"type": "order", "action": "order.processed",
               "data": {"id": "Ord1"}, "user_id": "42"}
    status = await proc.process(payload, b"{}", {"x-request-id": "r1"})
    assert status == 200
    assert svc.verified is not None
    assert svc.verified[1] == attempt.id

    # dedup key was stored lowercased
    assert "ord1:order.processed" in repo.events

    # a second webhook with yet another casing variant should dedupe against
    # the same lowercased event id and NOT call verify again
    svc.verified = None
    payload2 = {"type": "order", "action": "order.processed",
                "data": {"id": "ORD1"}, "user_id": "42"}
    status2 = await proc.process(payload2, b"{}", {"x-request-id": "r2"})
    assert status2 == 200
    assert svc.verified is None


@pytest.mark.asyncio
async def test_process_attempt_not_found_marks_processed_without_verify(monkeypatch):
    monkeypatch.setenv("MP_WEBHOOK_SECRET", "s")
    from infra.config import settings as sm; sm.get_settings.cache_clear()
    from application.services.pix.webhook_processor import MercadoPagoWebhookProcessor
    market_id = uuid.uuid4()
    # attempt repo holds an attempt for a different order, so lookup returns None
    attempt = Attempt(market_id, "some-other-order")
    repo = FakeWebhookRepo()
    svc = FakeService()
    proc = MercadoPagoWebhookProcessor(repo, FakeAttemptRepo(attempt), FakeConnRepo(), svc)
    payload = {"type": "order", "action": "order.processed",
               "data": {"id": "ORD1"}, "user_id": "42"}
    status = await proc.process(payload, b"{}", {"x-request-id": "r1"})
    assert status == 200
    assert svc.verified is None
    event = repo.events["ord1:order.processed"]
    assert event.processing_status == "processed"


@pytest.mark.asyncio
async def test_process_tenant_mismatch_different_user_id_skips_verify(monkeypatch):
    monkeypatch.setenv("MP_WEBHOOK_SECRET", "s")
    from infra.config import settings as sm; sm.get_settings.cache_clear()
    from application.services.pix.webhook_processor import MercadoPagoWebhookProcessor
    market_id = uuid.uuid4()
    attempt = Attempt(market_id, "ord1")
    repo = FakeWebhookRepo()
    svc = FakeService()
    proc = MercadoPagoWebhookProcessor(repo, FakeAttemptRepo(attempt), FakeConnRepo(), svc)
    # payload user_id ("99") differs from the connection's mp_user_id ("42")
    payload = {"type": "order", "action": "order.processed",
               "data": {"id": "ORD1"}, "user_id": "99"}
    status = await proc.process(payload, b"{}", {"x-request-id": "r1"})
    assert status == 200
    assert svc.verified is None
    event = repo.events["ord1:order.processed"]
    assert event.processing_status == "processed"


@pytest.mark.asyncio
async def test_process_tenant_mismatch_missing_user_id_fails_closed(monkeypatch):
    """Directly proves Fix 1: a webhook payload that omits user_id entirely
    must be treated as a tenant-anchor failure (fail closed), not skipped.
    Under the pre-fix `payload_user and ...` short-circuit, this payload
    would have sailed past the anchor check and called verify()."""
    monkeypatch.setenv("MP_WEBHOOK_SECRET", "s")
    from infra.config import settings as sm; sm.get_settings.cache_clear()
    from application.services.pix.webhook_processor import MercadoPagoWebhookProcessor
    market_id = uuid.uuid4()
    attempt = Attempt(market_id, "ord1")
    repo = FakeWebhookRepo()
    svc = FakeService()
    proc = MercadoPagoWebhookProcessor(repo, FakeAttemptRepo(attempt), FakeConnRepo(), svc)
    # no "user_id" key at all in the payload
    payload = {"type": "order", "action": "order.processed", "data": {"id": "ORD1"}}
    status = await proc.process(payload, b"{}", {"x-request-id": "r1"})
    assert status == 200
    assert svc.verified is None
    event = repo.events["ord1:order.processed"]
    assert event.processing_status == "processed"


@pytest.mark.asyncio
async def test_process_missing_data_id_returns_200_without_event(monkeypatch):
    monkeypatch.setenv("MP_WEBHOOK_SECRET", "s")
    from infra.config import settings as sm; sm.get_settings.cache_clear()
    from application.services.pix.webhook_processor import MercadoPagoWebhookProcessor
    market_id = uuid.uuid4()
    attempt = Attempt(market_id, "ord1")
    repo = FakeWebhookRepo()
    svc = FakeService()
    proc = MercadoPagoWebhookProcessor(repo, FakeAttemptRepo(attempt), FakeConnRepo(), svc)
    payload = {"type": "order", "action": "order.processed", "data": {}}  # no data.id
    status = await proc.process(payload, b"{}", {})
    assert status == 200
    assert svc.verified is None
    assert repo.events == {}


@pytest.mark.asyncio
async def test_process_missing_action_returns_200_without_event(monkeypatch):
    monkeypatch.setenv("MP_WEBHOOK_SECRET", "s")
    from infra.config import settings as sm; sm.get_settings.cache_clear()
    from application.services.pix.webhook_processor import MercadoPagoWebhookProcessor
    market_id = uuid.uuid4()
    attempt = Attempt(market_id, "ord1")
    repo = FakeWebhookRepo()
    svc = FakeService()
    proc = MercadoPagoWebhookProcessor(repo, FakeAttemptRepo(attempt), FakeConnRepo(), svc)
    payload = {"type": "order", "data": {"id": "ORD1"}}  # no action
    status = await proc.process(payload, b"{}", {})
    assert status == 200
    assert svc.verified is None
    assert repo.events == {}


@pytest.mark.asyncio
async def test_process_exception_is_caught_marks_failed_and_returns_200(monkeypatch):
    monkeypatch.setenv("MP_WEBHOOK_SECRET", "s")
    from infra.config import settings as sm; sm.get_settings.cache_clear()
    from application.services.pix.webhook_processor import MercadoPagoWebhookProcessor
    market_id = uuid.uuid4()
    attempt = Attempt(market_id, "ord1")
    repo = FakeWebhookRepo()
    svc = FakeService()
    proc = MercadoPagoWebhookProcessor(repo, FakeAttemptRepoRaises(), FakeConnRepo(), svc)
    payload = {"type": "order", "action": "order.processed",
               "data": {"id": "ORD1"}, "user_id": "42"}
    status = await proc.process(payload, b"{}", {"x-request-id": "r1"})
    assert status == 200
    assert svc.verified is None
    # the event was created before the raise, so mark_failed must have been
    # attempted for it
    event = repo.events["ord1:order.processed"]
    assert event.id in repo.mark_failed_calls
