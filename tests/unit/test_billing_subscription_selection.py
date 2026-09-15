from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from application.services.billing_subscription_selection import select_current_subscription


@dataclass
class StubSub:
    status: str
    updated_at: datetime
    expires_at: datetime | None = None
    cancel_at_period_end: bool = False
    id: uuid.UUID = field(default_factory=uuid.uuid4)


def test_returns_none_for_empty_list():
    assert select_current_subscription([]) is None


def test_prefers_active_over_abandoned_pending():
    now = datetime.utcnow()
    active = StubSub(status="active", updated_at=now - timedelta(days=10), expires_at=now + timedelta(days=20))
    abandoned_pending = StubSub(status="pending", updated_at=now)
    result = select_current_subscription([abandoned_pending, active])
    assert result is active


def test_canceled_at_period_end_with_time_left_beats_a_new_pending_checkout():
    now = datetime.utcnow()
    canceling = StubSub(
        status="active", updated_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=5), cancel_at_period_end=True,
    )
    new_pending = StubSub(status="pending", updated_at=now)
    result = select_current_subscription([new_pending, canceling])
    assert result is canceling


def test_among_same_priority_picks_most_recently_updated():
    now = datetime.utcnow()
    older = StubSub(status="pending", updated_at=now - timedelta(hours=2))
    newer = StubSub(status="pending", updated_at=now)
    result = select_current_subscription([older, newer])
    assert result is newer
