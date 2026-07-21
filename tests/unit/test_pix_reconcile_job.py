# ruff: noqa: E402
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import uuid
import pytest
from datetime import datetime, timezone, timedelta


class Attempt:
    def __init__(self, market_id):
        self.id = uuid.uuid4(); self.market_id = market_id; self.status = "pending"


class FakeAttemptRepo:
    def __init__(self, attempts): self._a = attempts
    async def list_active_stale(self, older_than, limit=50): return self._a


class FakeService:
    def __init__(self): self.calls = []
    async def verify(self, *, market_id, attempt_id, source):
        self.calls.append((attempt_id, source))
        class A: status = "approved"
        return A()


@pytest.mark.asyncio
async def test_reconcile_verifies_each_stale_attempt():
    from application.jobs.pix_jobs import PixReconciler
    market_id = uuid.uuid4()
    attempts = [Attempt(market_id), Attempt(market_id)]
    svc = FakeService()
    rec = PixReconciler(FakeAttemptRepo(attempts), svc)
    result = await rec.reconcile(now=datetime.now(timezone.utc))
    assert result["processed"] == 2
    assert all(src == "reconciliation" for _, src in svc.calls)


class FakeServiceWithFailure:
    def __init__(self): self.calls = []
    async def verify(self, *, market_id, attempt_id, source):
        self.calls.append(attempt_id)
        if len(self.calls) == 1:
            raise RuntimeError("boom")
        class A: status = "approved"
        return A()


@pytest.mark.asyncio
async def test_reconcile_continues_after_individual_failure():
    from application.jobs.pix_jobs import PixReconciler
    market_id = uuid.uuid4()
    attempts = [Attempt(market_id), Attempt(market_id)]
    svc = FakeServiceWithFailure()
    rec = PixReconciler(FakeAttemptRepo(attempts), svc)
    result = await rec.reconcile(now=datetime.now(timezone.utc))
    assert result["scanned"] == 2
    assert result["processed"] == 1
    assert len(svc.calls) == 2
