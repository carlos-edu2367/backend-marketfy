from __future__ import annotations

import os
import sys
import uuid

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from domain.identity import User, Market, UserRole
from domain.shared import Email


def test_user_can_be_created_without_cpf():
    user = User(name="Ana", email=Email("ana@t.com"), cpf=None, password_hash="x", role=UserRole.OWNER)
    assert user.cpf is None


def test_market_document_is_a_plain_string():
    market = Market(owner_id=uuid.uuid4(), name="Loja", document="12345678000195", address="Rua X")
    assert market.document == "12345678000195"
    assert isinstance(market.document, str)


import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from infra.database.models import Base, UserModel
from infra.repositories.sqlalchemy_repos import SQLAlchemyUserRepository


@pytest.mark.asyncio
async def test_user_repository_loads_a_user_with_null_cpf_without_crashing():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as session:
        model = UserModel(
            name="Ana", email="ana@t.com", cpf=None, password_hash="x", role="owner",
        )
        session.add(model)
        await session.commit()
        await session.refresh(model)

        repo = SQLAlchemyUserRepository(session)
        loaded = await repo.get_by_id(model.id)

        assert loaded is not None
        assert loaded.cpf is None
