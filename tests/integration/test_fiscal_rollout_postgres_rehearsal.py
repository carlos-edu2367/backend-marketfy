"""PostgreSQL rehearsal coverage for reversible fiscal-rule rollout gates."""
from __future__ import annotations

import os
import sys
import uuid
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


APP_DIR = Path(__file__).resolve().parents[2] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.append(str(APP_DIR))

TEST_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not TEST_POSTGRES_URL,
    reason="TEST_POSTGRES_URL is required for PostgreSQL rollout rehearsal",
)
os.environ.setdefault("DATABASE_URL", TEST_POSTGRES_URL or "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

from domain.fiscal import FiscalRuleEnforcement  # noqa: E402
from application.services.fiscal.fiscal_rollout_service import FiscalRolloutService  # noqa: E402
from application.services.fiscal.tax_rule_service import TaxRuleService  # noqa: E402
from infra.repositories.fiscal_repo import SQLAlchemyFiscalTenantConfigRepository  # noqa: E402
from infra.repositories.fiscal_repo import SQLAlchemyProductTaxRuleRepository  # noqa: E402
from infra.repositories.sqlalchemy_repos import SQLAlchemyProductRepository  # noqa: E402


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.mark.asyncio
async def test_postgresql_legacy_null_enforcement_is_read_as_off() -> None:
    """A restored pre-migration config must remain legacy-safe without a write."""
    engine = create_async_engine(_async_url(TEST_POSTGRES_URL), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    owner_id, market_id, config_id = (uuid.uuid4() for _ in range(3))

    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(text(
                "ALTER TABLE fiscal_tenant_configs "
                "ALTER COLUMN fiscal_rule_enforcement DROP NOT NULL"
            ))
            await connection.execute(text(
                "INSERT INTO users (id, name, email, password_hash, role, is_active) "
                "VALUES (:id, 'Legacy owner', :email, 'hash', 'owner', true)"
            ), {"id": owner_id, "email": f"legacy-{owner_id}@example.test"})
            await connection.execute(text(
                "INSERT INTO markets (id, owner_id, name, document, address, is_active) "
                "VALUES (:id, :owner_id, 'Legacy market', :document, 'Rua teste', true)"
            ), {
                "id": market_id,
                "owner_id": owner_id,
                "document": str(market_id.int)[:14],
            })
            await connection.execute(text(
                "INSERT INTO fiscal_tenant_configs (id, market_id, provider, environment, enabled, "
                "fiscal_rule_enforcement, nfce_series, nfce_next_number, numbering_mode, "
                "contingency_series, contingency_next_number, default_cfop, validation_status) "
                "VALUES (:id, :market_id, 'focus_nfe', 'homologacao', false, NULL, 1, 1, "
                "'provider_auto', 900, 1, '5102', 'not_validated')"
            ), {"id": config_id, "market_id": market_id})

            async with session_factory(bind=connection) as session:
                config = await SQLAlchemyFiscalTenantConfigRepository(session).get_by_market(market_id)

            assert config is not None
            assert config.fiscal_rule_enforcement is FiscalRuleEnforcement.OFF
            persisted = await connection.scalar(text(
                "SELECT fiscal_rule_enforcement FROM fiscal_tenant_configs WHERE id = :id"
            ), {"id": config_id})
            assert persisted is None
        finally:
            await transaction.rollback()
    await engine.dispose()


class _FlushOnlyFiscalTenantConfigRepository(SQLAlchemyFiscalTenantConfigRepository):
    """Keeps the rehearsal entirely inside its outer PostgreSQL transaction."""

    async def save(self, config, commit: bool = True):
        return await super().save(config, commit=False)


@pytest.mark.asyncio
async def test_postgresql_rollout_rehearsal_is_read_only_for_history_and_reversible() -> None:
    """Pendency reporting cannot alter history; only block-to-warn is rollback."""
    engine = create_async_engine(_async_url(TEST_POSTGRES_URL), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    owner_id, market_id, product_id, config_id, terminal_id, box_id, sale_id, document_id = (
        uuid.uuid4() for _ in range(8)
    )
    snapshot_sha256 = "a" * 64
    payload_sha256 = "b" * 64

    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(text(
                "INSERT INTO users (id, name, email, password_hash, role, is_active) "
                "VALUES (:id, 'Rehearsal owner', :email, 'hash', 'owner', true)"
            ), {"id": owner_id, "email": f"rehearsal-{owner_id}@example.test"})
            await connection.execute(text(
                "INSERT INTO markets (id, owner_id, name, document, address, is_active) "
                "VALUES (:id, :owner_id, 'Rehearsal market', :document, 'Rua teste', true)"
            ), {
                "id": market_id,
                "owner_id": owner_id,
                "document": str(market_id.int)[:14],
            })
            await connection.execute(text(
                "INSERT INTO products (id, market_id, name, code, price, active) "
                "VALUES (:id, :market_id, 'Produto sem regra', :code, 10.00, true)"
            ), {"id": product_id, "market_id": market_id, "code": f"SKU-{product_id}"})
            await connection.execute(text(
                "INSERT INTO fiscal_tenant_configs (id, market_id, provider, environment, enabled, "
                "fiscal_rule_enforcement, nfce_series, nfce_next_number, numbering_mode, "
                "contingency_series, contingency_next_number, default_cfop, validation_status) "
                "VALUES (:id, :market_id, 'focus_nfe', 'homologacao', false, 'off', 1, 1, "
                "'provider_auto', 900, 1, '5102', 'not_validated')"
            ), {"id": config_id, "market_id": market_id})
            await connection.execute(text(
                "INSERT INTO terminals (id, market_id, name, active) "
                "VALUES (:id, :market_id, 'Caixa rehearsal', true)"
            ), {"id": terminal_id, "market_id": market_id})
            await connection.execute(text(
                "INSERT INTO boxes (id, market_id, terminal_id, operator_id, status, "
                "initial_balance, current_balance) "
                "VALUES (:id, :market_id, :terminal_id, :owner_id, 'aberto', 0, 0)"
            ), {
                "id": box_id,
                "market_id": market_id,
                "terminal_id": terminal_id,
                "owner_id": owner_id,
            })
            await connection.execute(text(
                "INSERT INTO sales (id, market_id, box_id, operator_id, status, total_amount) "
                "VALUES (:id, :market_id, :box_id, :owner_id, 'concluida', 10.00)"
            ), {"id": sale_id, "market_id": market_id, "box_id": box_id, "owner_id": owner_id})
            await connection.execute(text(
                "INSERT INTO sale_items (id, sale_id, product_id, product_name_snapshot, quantity, "
                "unit_price, total, fiscal_tax_snapshot_json, snapshot_sha256) "
                "VALUES (:id, :sale_id, :product_id, 'Produto histórico', 1, 10.00, 10.00, "
                "CAST(:snapshot AS json), :snapshot_sha256)"
            ), {
                "id": uuid.uuid4(),
                "sale_id": sale_id,
                "product_id": product_id,
                "snapshot": '{\"cfop\":\"5405\"}',
                "snapshot_sha256": snapshot_sha256,
            })
            await connection.execute(text(
                "INSERT INTO fiscal_documents (id, market_id, sale_id, document_type, provider, "
                "environment, status, request_payload_json, request_payload_sha256) "
                "VALUES (:id, :market_id, :sale_id, 'nfce', 'focus_nfe', 'homologacao', 'queued', "
                "CAST(:payload AS json), :payload_sha256)"
            ), {
                "id": document_id,
                "market_id": market_id,
                "sale_id": sale_id,
                "payload": '{\"contract_version\":\"marketfy.fiscal-tax-snapshot.v2\"}',
                "payload_sha256": payload_sha256,
            })
            history_before = await connection.execute(text(
                "SELECT si.snapshot_sha256, fd.request_payload_sha256, fd.request_payload_json "
                "FROM sale_items si JOIN fiscal_documents fd ON fd.sale_id = si.sale_id "
                "WHERE fd.id = :document_id"
            ), {"document_id": document_id})
            history_before = history_before.one()

            async with session_factory(bind=connection) as session:
                products = await SQLAlchemyProductRepository(session).list_by_market(market_id)
                report = await TaxRuleService(SQLAlchemyProductTaxRuleRepository(session)).list_pendencies(
                    market_id=market_id,
                    products=products,
                    when=date.today(),
                    issuer_regime="simples_nacional",
                    destination_uf="GO",
                    document_model="65",
                )
                assert report.summary == {"missing": 1, "legacy_only": 0, "configured": 0,
                                          "draft": 0, "not_yet_effective": 0, "expired": 0,
                                          "context_mismatch": 0, "total": 1}

                rollout = FiscalRolloutService(
                    _FlushOnlyFiscalTenantConfigRepository(session),
                    readiness_provider=lambda _market_id: True,
                )
                assert await rollout.transition(market_id=market_id, requested="warn") is FiscalRuleEnforcement.WARN
                assert await rollout.transition(market_id=market_id, requested="block") is FiscalRuleEnforcement.BLOCK
                assert await rollout.transition(market_id=market_id, requested="warn") is FiscalRuleEnforcement.WARN
                await session.flush()

            history_after = await connection.execute(text(
                "SELECT si.snapshot_sha256, fd.request_payload_sha256, fd.request_payload_json "
                "FROM sale_items si JOIN fiscal_documents fd ON fd.sale_id = si.sale_id "
                "WHERE fd.id = :document_id"
            ), {"document_id": document_id})
            assert history_after.one() == history_before
            assert await connection.scalar(text(
                "SELECT fiscal_rule_enforcement FROM fiscal_tenant_configs WHERE id = :id"
            ), {"id": config_id}) == "warn"
        finally:
            await transaction.rollback()
    await engine.dispose()
