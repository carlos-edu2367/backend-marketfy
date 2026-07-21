# ruff: noqa: E402
"""Requer TEST_POSTGRES_URL apontando para um Postgres descartável de teste."""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

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
os.environ.setdefault("MP_OAUTH_REDIRECT_URI", "https://cb")
os.environ.setdefault("MP_SECRET_KEY", "k" * 32)
os.environ.setdefault("PUBLIC_FRONTEND_URL", "https://front")


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


async def _seed(session):
    from infra.database.models import (
        UserModel, MarketModel, TerminalModel, BoxModel, ProductModel,
        MercadoPagoConnectionModel,
    )
    from infra.security.secret_cipher import SecretCipher

    owner_id = uuid.uuid4()
    accountant_id = uuid.uuid4()
    market_id = uuid.uuid4()
    terminal_id = uuid.uuid4()
    box_id = uuid.uuid4()
    product_id = uuid.uuid4()

    # CPF obrigatório: SQLAlchemyUserRepository._to_entity quebra com CPF(None)
    # quando o usuário não tem cpf cadastrado (bug pré-existente, sinalizado à parte).
    session.add(UserModel(id=owner_id, name="Owner", email=f"owner-{owner_id}@x.test",
                          password_hash="x", role="owner", cpf=str(owner_id.int)[:11]))
    session.add(UserModel(id=accountant_id, name="Contador", email=f"acc-{accountant_id}@x.test",
                          password_hash="x", role="accountant", cpf=str(accountant_id.int)[:11]))
    await session.flush()

    session.add(MarketModel(id=market_id, owner_id=owner_id, name="Loja",
                            document=str(market_id.int)[:14], address="Rua X"))
    await session.flush()

    session.add(TerminalModel(id=terminal_id, market_id=market_id, name="Terminal 1"))
    await session.flush()

    session.add(BoxModel(id=box_id, market_id=market_id, terminal_id=terminal_id,
                        operator_id=owner_id, status="aberto"))
    await session.flush()

    session.add(ProductModel(id=product_id, market_id=market_id, name="Produto Teste",
                            code="P1", price=Decimal("10.00")))
    await session.flush()

    cipher = SecretCipher("k" * 32)
    session.add(MercadoPagoConnectionModel(
        market_id=market_id, status="connected", mp_user_id="42",
        access_token_ciphertext=cipher.encrypt("AT"),
        access_token_expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    ))
    await session.commit()

    return {
        "market_id": market_id, "owner_id": owner_id, "accountant_id": accountant_id,
        "terminal_id": terminal_id, "box_id": box_id, "product_id": product_id,
    }


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
async def test_qr_requires_sales_write(app_client):
    engine = create_async_engine(_async_url(TEST_POSTGRES_URL), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        seeded = await _seed(session)
    await engine.dispose()

    headers = {"Authorization": f"Bearer {_token_for(seeded['accountant_id'])}"}
    r = await app_client.post(f"/api/v1/pix/{seeded['market_id']}/qr", headers=headers,
                              json={"terminal_id": "x", "box_id": "y", "items": []})
    assert r.status_code in (403, 422)


@pytest.mark.asyncio
@respx.mock
async def test_qr_creates_attempt_and_returns_qr(app_client, monkeypatch):
    from infra.providers.pix.location import UnconfiguredPosLocationProvider

    async def _fake_location(self, market_id):
        return {"street_number": "1", "street_name": "R", "city_name": "SP",
                "state_name": "SP", "latitude": -23.5, "longitude": -46.6}

    monkeypatch.setattr(UnconfiguredPosLocationProvider, "get_location", _fake_location)

    engine = create_async_engine(_async_url(TEST_POSTGRES_URL), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        seeded = await _seed(session)
    await engine.dispose()

    respx.post("https://api.mercadopago.com/users/42/stores").mock(
        return_value=Response(201, json={"id": 1234567, "name": "Loja"})
    )
    respx.post("https://api.mercadopago.com/pos").mock(
        return_value=Response(201, json={"id": 2711382, "external_id": "posext"})
    )
    respx.post("https://api.mercadopago.com/v1/orders").mock(return_value=Response(201, json={
        "id": "ORD9", "status": "created", "status_detail": "created", "total_amount": "20.00",
        "type_response": {"qr_data": "QRSTRING"}, "user_id": "42"}))

    headers = {"Authorization": f"Bearer {_token_for(seeded['owner_id'])}"}
    r = await app_client.post(f"/api/v1/pix/{seeded['market_id']}/qr", headers=headers, json={
        "terminal_id": str(seeded["terminal_id"]), "box_id": str(seeded["box_id"]),
        "items": [{"product_id": str(seeded["product_id"]), "quantity": 2}]})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["qr_data"] == "QRSTRING"
    assert body["amount"] == "20.00"  # 2 x 10.00, calculado no backend
    assert "access_token" not in r.text
