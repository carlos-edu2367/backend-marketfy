# ruff: noqa: E402
"""Requer TEST_POSTGRES_URL apontando para um Postgres descartável de teste."""
import os
import sys
import uuid
from datetime import timedelta
from pathlib import Path

app_dir = Path(__file__).resolve().parents[2] / "app"
if str(app_dir) not in sys.path:
    sys.path.append(str(app_dir))

TEST_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.skipif(
    not TEST_POSTGRES_URL,
    reason="TEST_POSTGRES_URL is required for PostgreSQL integration tests",
)
os.environ.setdefault("DATABASE_URL", TEST_POSTGRES_URL or "postgresql://x:y@localhost/z")
os.environ.setdefault("SECRET_KEY", "test-secret-key-com-mais-de-32-caracteres-ok")
os.environ.setdefault("MP_APP_ID", "app-1")
os.environ.setdefault("MP_OAUTH_REDIRECT_URI", "https://cb")
os.environ.setdefault("MP_SECRET_KEY", "k" * 32)
os.environ.setdefault("PUBLIC_FRONTEND_URL", "https://front")


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _seed_market_owner_and_cashier(session):
    from infra.database.models import UserModel, MarketModel, MarketMemberModel

    owner_id = uuid.uuid4()
    cashier_id = uuid.uuid4()
    market_id = uuid.uuid4()
    # CPF obrigatório aqui: SQLAlchemyUserRepository._to_entity quebra com
    # CPF(None) quando o usuário não tem cpf cadastrado (bug pré-existente,
    # fora do escopo deste plano — sinalizado separadamente).
    session.add(UserModel(id=owner_id, name="Owner", email=f"owner-{owner_id}@x.test",
                          password_hash="x", role="owner", cpf=str(owner_id.int)[:11]))
    session.add(UserModel(id=cashier_id, name="Cashier", email=f"cashier-{cashier_id}@x.test",
                          password_hash="x", role="cashier", cpf=str(cashier_id.int)[:11]))
    await session.flush()
    session.add(MarketModel(id=market_id, owner_id=owner_id, name="Loja",
                            document=str(market_id.int)[:14], address="Rua X"))
    await session.flush()
    session.add(MarketMemberModel(market_id=market_id, user_id=cashier_id, role="cashier"))
    await session.commit()
    return market_id, owner_id, cashier_id


def _token_for(user_id: uuid.UUID) -> str:
    from infra.security.auth_handler import AuthHandler
    return AuthHandler.create_access_token(data={"sub": str(user_id)})


@pytest_asyncio.fixture
async def app_client():
    from infra.web.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_authorize_requires_permission_and_status_defaults(app_client):
    engine = create_async_engine(_async_url(TEST_POSTGRES_URL), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        market_id, owner_id, cashier_id = await _seed_market_owner_and_cashier(session)

    owner_headers = {"Authorization": f"Bearer {_token_for(owner_id)}"}
    cashier_headers = {"Authorization": f"Bearer {_token_for(cashier_id)}"}

    # cashier não tem PAYMENTS_WRITE
    r = await app_client.post(f"/api/v1/pix/{market_id}/oauth/authorize", headers=cashier_headers)
    assert r.status_code == 403

    # owner recebe a URL de autorização
    r = await app_client.post(f"/api/v1/pix/{market_id}/oauth/authorize", headers=owner_headers)
    assert r.status_code == 200
    assert "auth.mercadopago.com/authorization" in r.json()["authorization_url"]

    # status ainda not_connected
    r = await app_client.get(f"/api/v1/pix/{market_id}/oauth/status", headers=owner_headers)
    assert r.status_code == 200
    assert r.json()["status"] == "not_connected"

    await engine.dispose()
