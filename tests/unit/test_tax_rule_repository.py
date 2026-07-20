from __future__ import annotations

import os
import sys
import uuid
from datetime import date, datetime

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

    def all(self):
        return self._values


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


def test_repository_converts_frozen_evidence_to_native_json_containers() -> None:
    from domain.fiscal_tax import ProductTaxRule, TaxRegime, TaxRuleStatus
    from infra.repositories.fiscal_repo import SQLAlchemyProductTaxRuleRepository

    rule = ProductTaxRule(
        market_id=MARKET_ID,
        name="Bebidas ST",
        status=TaxRuleStatus.PUBLISHED,
        effective_from=date(2026, 7, 1),
        issuer_regime=TaxRegime.SIMPLES_NACIONAL,
        destination_uf="GO",
        document_model="65",
        tax_parameters={"icms": {"rates": ["18.00"]}},
        approval={"review": {"items": [{"field": "ncm"}]}},
    )
    model = ProductTaxRuleModel(id=rule.id)

    SQLAlchemyProductTaxRuleRepository._copy_rule_to_model(rule, model)

    assert type(model.tax_parameters_json) is dict
    assert type(model.tax_parameters_json["icms"]["rates"]) is list
    assert model.tax_parameters_json == {"icms": {"rates": ["18.00"]}}
    assert type(model.approval_json) is dict
    assert type(model.approval_json["review"]["items"]) is list
    assert model.approval_json == {"review": {"items": [{"field": "ncm"}]}}


class PublishingSession:
    def __init__(self, model):
        self.model = model
        self.get_calls = []
        self.added = []
        self.commits = 0

    async def get(self, model_type, key, **kwargs):
        self.get_calls.append((model_type, key, kwargs))
        return self.model

    def add(self, model):
        self.added.append(model)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _model):
        return None


@pytest.mark.asyncio
async def test_publication_locks_and_rejects_a_draft_changed_after_review() -> None:
    from domain.fiscal import FiscalRuleError, TaxRuleApproval
    from infra.repositories.fiscal_repo import (
        SQLAlchemyProductTaxRuleRepository,
        _to_product_tax_rule,
    )

    reviewed_at = datetime(2026, 7, 15, 12, 0, 0)
    model = _rule_model()
    model.status = "draft"
    model.cest = "0300700"
    model.icms_csosn = "500"
    model.pis_cst = "07"
    model.cofins_cst = "07"
    model.tax_parameters_json = {
        "icms_mode": "retained_st",
        "pis": {
            "group": "PIS07", "cst": "07", "base": "0.00",
            "rate": "0.0000", "amount": "0.00",
        },
        "cofins": {
            "group": "COFINS07", "cst": "07", "base": "0.00",
            "rate": "0.0000", "amount": "0.00",
        },
    }
    model.approval_json = {"reference": "Decreto GO", "checksum": "a" * 64}
    model.created_at = reviewed_at
    model.updated_at = reviewed_at
    reviewed_rule = _to_product_tax_rule(model)

    model.name = "Editada concorrentemente"
    model.updated_at = datetime(2026, 7, 15, 12, 1, 0)
    session = PublishingSession(model)
    approval = TaxRuleApproval.from_verified_artifact(
        rule_id=reviewed_rule.id,
        accountant_user_id=uuid.uuid4(),
        homologation_xml_storage_key=f"fiscal/homologacao/{MARKET_ID}/authorized.xml",
        canonical_xml=b"<NFe/>",
    )

    with pytest.raises(FiscalRuleError) as error:
        await SQLAlchemyProductTaxRuleRepository(session).publish_rule_with_approval(
            reviewed_rule, approval
        )

    assert error.value.code == "tax_rule.stale_review"
    assert session.get_calls == [
        (ProductTaxRuleModel, reviewed_rule.id, {"with_for_update": True})
    ]
    assert model.name == "Editada concorrentemente"
    assert model.status == "draft"
    assert session.added == []
    assert session.commits == 0


@pytest.mark.asyncio
async def test_pendency_history_query_is_tenant_scoped_and_returns_raw_associations() -> None:
    from types import SimpleNamespace

    from infra.repositories.fiscal_repo import SQLAlchemyProductTaxRuleRepository

    assignment_id = uuid.uuid4()
    assignment = SimpleNamespace(
        id=assignment_id,
        market_id=MARKET_ID,
        product_id=PRODUCT_ID,
        effective_from=date(2026, 7, 1),
        effective_to=None,
    )
    session = CapturingSession([(assignment, _rule_model())])

    result = await SQLAlchemyProductTaxRuleRepository(
        session
    ).list_product_rule_associations(MARKET_ID, [PRODUCT_ID])

    association = result[PRODUCT_ID][0]
    assert association.association_id == assignment_id
    assert association.effective_from == date(2026, 7, 1)
    assert association.rule.market_id == MARKET_ID
    sql = _compiled_sql(session.statement)
    assert "product_tax_rule_assignments.market_id" in sql
    assert "product_tax_rules.market_id" in sql
    assert str(MARKET_ID) in sql
    assert str(PRODUCT_ID) in sql
