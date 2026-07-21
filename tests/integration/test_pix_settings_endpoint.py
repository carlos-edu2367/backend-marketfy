# ruff: noqa: E402
"""Requer TEST_POSTGRES_URL apontando para um Postgres descartável de teste."""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
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


async def _seed(session):
    from infra.database.models import UserModel, MarketModel, MercadoPagoConnectionModel
    from infra.security.secret_cipher import SecretCipher

    owner_id = uuid.uuid4()
    market_id = uuid.uuid4()

    session.add(UserModel(id=owner_id, name="Owner", email=f"owner-{owner_id}@x.test",
                          password_hash="x", role="owner", cpf=str(owner_id.int)[:11]))
    await session.flush()

    session.add(MarketModel(id=market_id, owner_id=owner_id, name="Loja",
                            document=str(market_id.int)[:14], address="Rua X"))
    await session.flush()

    cipher = SecretCipher("k" * 32)
    session.add(MercadoPagoConnectionModel(
        market_id=market_id, status="connected", mp_user_id="42",
        access_token_ciphertext=cipher.encrypt("AT"),
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    ))
    await session.commit()

    return {"market_id": market_id, "owner_id": owner_id}


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
async def test_settings_blocks_enable_without_fees_ack(app_client):
    engine = create_async_engine(_async_url(TEST_POSTGRES_URL), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        seeded = await _seed(session)
    await engine.dispose()

    headers = {"Authorization": f"Bearer {_token_for(seeded['owner_id'])}"}
    r = await app_client.put(f"/api/v1/pix/{seeded['market_id']}/settings", headers=headers,
                             json={"enabled_in_pdv": True, "fees_acknowledged": False})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_settings_ack_fees_then_enable(app_client):
    engine = create_async_engine(_async_url(TEST_POSTGRES_URL), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        seeded = await _seed(session)
    await engine.dispose()

    headers = {"Authorization": f"Bearer {_token_for(seeded['owner_id'])}"}
    r = await app_client.put(f"/api/v1/pix/{seeded['market_id']}/settings", headers=headers,
                             json={"enabled_in_pdv": True, "fees_acknowledged": True,
                                   "allowed_terminal_ids": []})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled_in_pdv"] is True
    assert body["allowed_terminal_ids"] == []
