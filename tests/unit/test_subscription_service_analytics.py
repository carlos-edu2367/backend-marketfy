from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from unittest.mock import AsyncMock

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest

from application.services.subscription_service import SubscriptionService


@dataclass
class StubPlan:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = "Trial"
    type: object = None  # setado no teste com PlanType.TRIAL real


@dataclass
class StubUser:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    plan_id: Optional[uuid.UUID] = None
    plan_expiration: Optional[datetime] = None
    is_active: bool = False


class PlanRepo:
    def __init__(self, plans):
        self._plans = plans
    async def list_all(self):
        return self._plans


class UserRepo:
    def __init__(self):
        self.saved = []
    async def save(self, user):
        self.saved.append(user)
        return user


@pytest.mark.asyncio
async def test_activate_trial_tracks_trial_activated_event():
    from application.services.subscription_service import PlanType

    plan = StubPlan(name="Trial 14 dias", type=PlanType.TRIAL)
    user = StubUser()
    analytics = AsyncMock()

    svc = SubscriptionService(UserRepo(), PlanRepo([plan]), analytics=analytics)
    await svc.activate_trial(user)

    analytics.track_event.assert_awaited_once_with(
        str(user.id), "trial_activated", {"plan_name": "Trial 14 dias"}
    )
