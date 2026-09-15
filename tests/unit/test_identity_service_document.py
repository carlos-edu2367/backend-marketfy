from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

import pytest

from application.dtos import UserCreateDTO, MarketCreateDTO
from application.services.identity_service import IdentityService
from domain.identity import User, UserRole


class FakeUserRepo:
    def __init__(self):
        self.saved = None

    async def get_by_email(self, email):
        return None

    async def save(self, user, commit=True):
        self.saved = user
        user.id = uuid.uuid4()
        return user

    async def get_by_id(self, user_id):
        return self.saved


class FakeMarketRepo:
    def __init__(self):
        self.saved = None

    async def count_by_owner(self, owner_id):
        return 0

    async def save(self, market, commit=True):
        self.saved = market
        market.id = uuid.uuid4()
        return market


class FakePlanRepo:
    def __init__(self, plan):
        self.plan = plan

    async def get_by_id(self, plan_id):
        return self.plan


def _owner_with_plan(plan_id):
    user = User(name="Ana", email=None, cpf=None, password_hash="x", role=UserRole.OWNER)
    user.id = uuid.uuid4()
    user.plan_id = plan_id
    user.plan_expiration = datetime.utcnow() + timedelta(days=30)
    return user


@pytest.mark.asyncio
async def test_register_user_without_cpf_succeeds():
    user_repo = FakeUserRepo()
    service = IdentityService(user_repo=user_repo, market_repo=FakeMarketRepo(), plan_repo=None, hasher=lambda p: p)
    dto = UserCreateDTO(name="Ana", email="ana@t.com", password="segredo1")

    await service.register_user(dto)

    assert user_repo.saved.cpf is None


@pytest.mark.asyncio
async def test_create_market_with_valid_cpf_stores_11_digits():
    plan_id = uuid.uuid4()

    class FakePlan:
        id = plan_id
        name = "Basico"
        max_markets = 5
        def is_limit_reached(self, current, kind):
            return False

    user_repo = FakeUserRepo()
    owner = _owner_with_plan(plan_id)
    user_repo.saved = owner
    market_repo = FakeMarketRepo()
    service = IdentityService(
        user_repo=user_repo, market_repo=market_repo, plan_repo=FakePlanRepo(FakePlan()), hasher=lambda p: p,
    )
    dto = MarketCreateDTO(name="Loja", document="111.444.777-35", address="Rua X")

    market = await service.create_market(owner.id, dto)

    assert market.document == "11144477735"


@pytest.mark.asyncio
async def test_create_market_with_valid_cnpj_stores_14_digits():
    plan_id = uuid.uuid4()

    class FakePlan:
        id = plan_id
        name = "Basico"
        max_markets = 5
        def is_limit_reached(self, current, kind):
            return False

    user_repo = FakeUserRepo()
    owner = _owner_with_plan(plan_id)
    user_repo.saved = owner
    market_repo = FakeMarketRepo()
    service = IdentityService(
        user_repo=user_repo, market_repo=market_repo, plan_repo=FakePlanRepo(FakePlan()), hasher=lambda p: p,
    )
    dto = MarketCreateDTO(name="Loja", document="12.345.678/0001-95", address="Rua X")

    market = await service.create_market(owner.id, dto)

    assert market.document == "12345678000195"
