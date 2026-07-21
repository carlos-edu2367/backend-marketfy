# ruff: noqa: E402
"""Requer TEST_POSTGRES_URL apontando para um Postgres descartável de teste."""
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse, parse_qs

app_dir = Path(__file__).resolve().parents[2] / "app"
if str(app_dir) not in sys.path:
    sys.path.append(str(app_dir))

TEST_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

import pytest
import pytest_asyncio
import respx
from httpx import AsyncClient, ASGITransport, Response
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.skipif(
    not TEST_POSTGRES_URL,
    reason="TEST_POSTGRES_URL is required for PostgreSQL integration tests",
)
os.environ.setdefault("DATABASE_URL", TEST_POSTGRES_URL or "postgresql://x:y@localhost/z")
os.environ.setdefault("SECRET_KEY", "test-secret-key-com-mais-de-32-caracteres-ok")
os.environ.setdefault("MP_APP_ID", "app-1")
os.environ.setdefault("MP_CLIENT_SECRET", "sec")
os.environ.setdefault("MP_OAUTH_REDIRECT_URI", "https://cb")
os.environ.setdefault("MP_SECRET_KEY", "k" * 32)
os.environ.setdefault("PUBLIC_FRONTEND_URL", "https://front")


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _seed_owner_and_market(session):
    from infra.database.models import UserModel, MarketModel

    owner_id = uuid.uuid4()
    market_id = uuid.uuid4()
    session.add(UserModel(id=owner_id, name="Owner", email=f"owner-{owner_id}@x.test",
                          password_hash="x", role="owner", cpf=str(owner_id.int)[:11]))
    await session.flush()
    session.add(MarketModel(id=market_id, owner_id=owner_id, name="Loja",
                            document=str(market_id.int)[:14], address="Rua X"))
    await session.commit()
    return market_id, owner_id


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
@respx.mock
async def test_full_oauth_connect_flow(app_client):
    engine = create_async_engine(_async_url(TEST_POSTGRES_URL), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        market_id, owner_id = await _seed_owner_and_market(session)

    owner_headers = {"Authorization": f"Bearer {_token_for(owner_id)}"}

    # 1. authorize -> extrai o state
    r = await app_client.post(f"/api/v1/pix/{market_id}/oauth/authorize", headers=owner_headers)
    assert r.status_code == 200
    state = parse_qs(urlparse(r.json()["authorization_url"]).query)["state"][0]

    # 2. MP troca o código
    respx.post("https://api.mercadopago.com/oauth/token").mock(return_value=Response(200, json={
        "access_token": "AT", "refresh_token": "RT", "expires_in": 15552000,
        "user_id": 42, "scope": "offline_access read write",
    }))

    # 3. callback (sem seguir redirect)
    cb = await app_client.get(f"/api/v1/pix/oauth/callback?code=CODE&state={state}",
                              follow_redirects=False)
    assert cb.status_code in (302, 307)
    assert "pix_oauth=success" in cb.headers["location"]

    # 4. status = connected, sem vazar token
    st = await app_client.get(f"/api/v1/pix/{market_id}/oauth/status", headers=owner_headers)
    body = st.json()
    assert body["status"] == "connected"
    assert "AT" not in st.text and "RT" not in st.text

    # 5. callback repetido com o mesmo state -> erro (uso único)
    cb2 = await app_client.get(f"/api/v1/pix/oauth/callback?code=CODE&state={state}",
                               follow_redirects=False)
    assert "pix_oauth=error" in cb2.headers["location"]

    await engine.dispose()
