# ruff: noqa: E402
import os
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


app_dir = Path(__file__).resolve().parents[2] / "app"
if str(app_dir) not in sys.path:
    sys.path.append(str(app_dir))

TEST_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not TEST_POSTGRES_URL,
    reason="TEST_POSTGRES_URL is required for PostgreSQL persistence integration",
)
os.environ.setdefault(
    "DATABASE_URL", TEST_POSTGRES_URL or "sqlite+aiosqlite:///:memory:"
)
os.environ.setdefault("SECRET_KEY", "test-secret-key")

from application.services.fiscal.snapshot_integrity import canonical_sha256
from domain.inventory import Product
from domain.sales import Sale, SaleItemFiscalEvidence
from infra.database.models import (
    BoxModel,
    MarketModel,
    ProductModel,
    TerminalModel,
    UserModel,
)
from infra.repositories.sqlalchemy_repos import SQLAlchemySaleRepository


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.mark.asyncio
async def test_postgresql_round_trip_preserves_evidence_and_warn_pendencies() -> None:
    engine = create_async_engine(_async_url(TEST_POSTGRES_URL), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    owner_id = uuid.uuid4()
    market_id = uuid.uuid4()
    terminal_id = uuid.uuid4()
    box_id = uuid.uuid4()
    product_id = uuid.uuid4()
    rule_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    snapshot = {
        "rule_id": str(rule_id),
        "rule_version": 4,
        "calculation_version": "marketfy-tax-calc.v2",
        "ncm": "22021000",
        "cfop": "5405",
        "icms": {"group": "ICMSSN500", "st_amount": "0.00"},
        "pis": {"group": "PIS07", "amount": "0.00"},
        "cofins": {"group": "COFINS07", "amount": "0.00"},
    }
    digest = canonical_sha256(snapshot)
    pendencies = [{
        "code": "sale.fiscal_rule_missing",
        "product_id": str(product_id),
        "product_name": "Produto pendente",
    }]

    async with session_factory() as session:
        session.add(UserModel(
            id=owner_id,
            name="Owner Teste",
            email=f"owner-{owner_id}@example.test",
            password_hash="test",
            role="owner",
        ))
        await session.flush()
        session.add(MarketModel(
            id=market_id,
            owner_id=owner_id,
            name="Mercado Teste",
            document=str(market_id.int)[:14],
            address="Rua Teste",
        ))
        await session.flush()
        session.add(TerminalModel(
            id=terminal_id,
            market_id=market_id,
            name="Caixa 1",
        ))
        session.add(ProductModel(
            id=product_id,
            market_id=market_id,
            name="Produto Persistido",
            code=f"SKU-{product_id}",
            price=Decimal("10.00"),
            current_stock=Decimal("5.000"),
            ncm="22021000",
        ))
        await session.flush()
        session.add(BoxModel(
            id=box_id,
            market_id=market_id,
            terminal_id=terminal_id,
            operator_id=owner_id,
            status="aberto",
        ))
        await session.flush()

        product = Product(
            id=product_id,
            market_id=market_id,
            name="Produto Persistido",
            code=f"SKU-{product_id}",
            barcode=None,
            price=Decimal("10.00"),
        )
        sale = Sale(
            market_id=market_id,
            box_id=box_id,
            operator_id=owner_id,
            synced_at=now,
            received_at=now,
            created_at=now,
            fiscal_rule_pendencies=pendencies,
        )
        sale.add_item(
            product,
            Decimal("1"),
            fiscal_evidence=SaleItemFiscalEvidence(
                tax_rule_id_snapshot=rule_id,
                tax_rule_version_snapshot=4,
                fiscal_calculation_version="marketfy-tax-calc.v2",
                fiscal_tax_snapshot=snapshot,
                snapshot_sha256=digest,
            ),
        )
        repository = SQLAlchemySaleRepository(session)

        await repository.save(sale, commit=False)
        await session.flush()
        session.expire_all()
        reloaded = await repository.get_by_id(sale.id)

        assert reloaded is not None
        assert reloaded.fiscal_rule_pendencies == pendencies
        assert reloaded.items[0].tax_rule_id_snapshot == rule_id
        assert reloaded.items[0].tax_rule_version_snapshot == 4
        assert reloaded.items[0].fiscal_calculation_version == "marketfy-tax-calc.v2"
        assert reloaded.items[0].fiscal_tax_snapshot == snapshot
        assert reloaded.items[0].snapshot_sha256 == digest
        assert canonical_sha256(reloaded.items[0].fiscal_tax_snapshot) == digest

        await session.rollback()
    await engine.dispose()
