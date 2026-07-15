from __future__ import annotations

import os
import sys
import uuid
from datetime import date

import pytest
from sqlalchemy.dialects import postgresql


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "../../app"))
if APP_DIR not in sys.path:
    sys.path.append(APP_DIR)
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key")


from domain.shared import BusinessRuleException
from infra.database.models import ProductTaxRuleModel


MARKET_ID = uuid.uuid4()
PRODUCT_ID = uuid.uuid4()
ON_DATE = date(2026, 7, 15)


class _Scalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _Result:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return _Scalars(self._values)


class CapturingSession:
    def __init__(self, rows):
        self.rows = rows
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return _Result(self.rows)


def _rule_model(*, rule_id=None, market_id=MARKET_ID):
    return ProductTaxRuleModel(
        id=rule_id or uuid.uuid4(),
        market_id=market_id,
        name="Bebidas ST",
        version=3,
        rule_family_id=uuid.uuid4(),
        status="published",
        effective_from=date(2026, 7, 1),
        effective_to=date(2026, 7, 31),
        issuer_regime="simples_nacional",
        destination_uf="GO",
        document_model="65",
        ncm="22021000",
        origin="0",
        cfop="5405",
        icms_group="ICMSSN500",
        tax_parameters_json={"icms_mode": "retained_st"},
        approval_json={"source": "accountant"},
    )


def _compiled_sql(statement) -> str:
    return " ".join(
        str(
            statement.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        ).split()
    )


@pytest.mark.asyncio
async def test_effective_rule_query_uses_assignment_history_and_all_safety_filters() -> None:
    from domain.interfaces import ProductTaxRuleRepositoryInterface
    from infra.repositories.fiscal_repo import (
        SQLAlchemyProductTaxRuleRepository as CanonicalRepository,
    )
    from infra.repositories.fiscal_tax_rule_repo import SQLAlchemyProductTaxRuleRepository

    assert SQLAlchemyProductTaxRuleRepository is CanonicalRepository
    assert issubclass(SQLAlchemyProductTaxRuleRepository, ProductTaxRuleRepositoryInterface)
    session = CapturingSession([])

    result = await SQLAlchemyProductTaxRuleRepository(session).get_effective_published_rule(
        market_id=MARKET_ID,
        product_id=PRODUCT_ID,
        on_date=ON_DATE,
    )

    assert result is None
    sql = _compiled_sql(session.statement)
    assert "product_tax_rule_assignments" in sql
    assert "products.tax_rule_id" not in sql
    assert str(MARKET_ID) in sql
    assert str(PRODUCT_ID) in sql
    assert "product_tax_rules.status = 'published'" in sql
    assert "product_tax_rule_assignments.effective_from <= '2026-07-15'" in sql
    assert "product_tax_rules.effective_from <= '2026-07-15'" in sql
    assert "FOR SHARE" in sql


@pytest.mark.asyncio
async def test_effective_rule_maps_v2_context_and_evidence_without_inference() -> None:
    from domain.fiscal_tax import TaxRegime
    from infra.repositories.fiscal_tax_rule_repo import SQLAlchemyProductTaxRuleRepository

    session = CapturingSession([_rule_model()])

    rule = await SQLAlchemyProductTaxRuleRepository(session).get_effective_published_rule(
        market_id=MARKET_ID,
        product_id=PRODUCT_ID,
        on_date=ON_DATE,
    )

    assert rule is not None
    assert rule.issuer_regime is TaxRegime.SIMPLES_NACIONAL
    assert rule.destination_uf == "GO"
    assert rule.document_model == "65"
    assert rule.tax_parameters == {"icms_mode": "retained_st"}
    assert rule.approval == {"source": "accountant"}


@pytest.mark.asyncio
async def test_effective_rule_fails_closed_on_ambiguous_history() -> None:
    from infra.repositories.fiscal_tax_rule_repo import SQLAlchemyProductTaxRuleRepository

    session = CapturingSession([_rule_model(), _rule_model()])

    with pytest.raises(BusinessRuleException, match="Mais de uma regra fiscal vigente"):
        await SQLAlchemyProductTaxRuleRepository(session).get_effective_published_rule(
            market_id=MARKET_ID,
            product_id=PRODUCT_ID,
            on_date=ON_DATE,
        )
