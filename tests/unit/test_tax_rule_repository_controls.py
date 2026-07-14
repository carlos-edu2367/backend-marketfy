"""Safety controls exercised against the production tax-rule repository."""
from __future__ import annotations

import os
import sys
import uuid
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "../../app"))
if APP_DIR not in sys.path:
    sys.path.append(APP_DIR)


MARKET_ID = uuid.uuid4()
PRODUCT_ID = uuid.uuid4()
ACTOR_ID = uuid.uuid4()


def published_st_rule(*, csosn: str = "500"):
    from domain.fiscal import ProductTaxRule, ProductTaxRuleStatus

    return ProductTaxRule(
        market_id=MARKET_ID,
        name="Refrigerante ST",
        status=ProductTaxRuleStatus.PUBLISHED,
        effective_from=date.today(),
        ncm="22021000",
        cest="0300700",
        origin="0",
        cfop="5405",
        icms_group="ICMSSN500",
        icms_csosn=csosn,
        icms_st_mod_bc="4",
        icms_st_mva_rate=Decimal("40.00"),
        icms_st_rate=Decimal("18.00"),
        pis_cst="07",
        pis_rate=Decimal("0.00"),
        cofins_cst="07",
        cofins_rate=Decimal("0.00"),
        approved_by=ACTOR_ID,
        approved_at=datetime.utcnow(),
    )


class _Scalars:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class _Result:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return _Scalars(self.values)


class AssignmentSession:
    """Minimal session that drives the real production repository method."""

    def __init__(self, *, existing_assignment=None):
        self.product = SimpleNamespace(
            id=PRODUCT_ID,
            market_id=MARKET_ID,
            deleted_at=None,
            tax_rule_id=existing_assignment.tax_rule_id if existing_assignment else None,
            updated_at=None,
        )
        self.assignments = [existing_assignment] if existing_assignment else []
        self.added = []

    async def execute(self, _query):
        if not hasattr(self, "_returned_products"):
            self._returned_products = True
            return _Result([self.product])
        return _Result(self.assignments)

    def add(self, model):
        self.added.append(model)
        self.assignments.append(model)

    async def commit(self):
        return None


def test_production_publication_validator_rejects_inconsistent_icmssn500_csosn(monkeypatch):
    monkeypatch.setenv("FISCAL_APPROVED_ICMS_GROUPS", "ICMSSN500")
    from domain.shared import BusinessRuleException
    from infra.config.settings import get_settings
    from infra.repositories.fiscal_repo import SQLAlchemyProductTaxRuleRepository

    get_settings.cache_clear()
    with pytest.raises(BusinessRuleException, match="CSOSN"):
        SQLAlchemyProductTaxRuleRepository._validate_for_publication(published_st_rule(csosn="102"))
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_production_repository_rejects_duplicate_product_id_before_creating_invalid_history():
    from domain.shared import BusinessRuleException
    from infra.repositories.fiscal_repo import SQLAlchemyProductTaxRuleRepository

    session = AssignmentSession()
    repository = SQLAlchemyProductTaxRuleRepository(session)

    with pytest.raises(BusinessRuleException, match="duplicado"):
        await repository.assign_published_rule(
            market_id=MARKET_ID,
            product_ids=[PRODUCT_ID, PRODUCT_ID],
            rule=published_st_rule(),
            effective_from=date.today(),
            actor_id=ACTOR_ID,
            reason="Aprovação contábil",
        )

    assert session.added == []


@pytest.mark.asyncio
async def test_production_repository_keeps_same_day_identical_assignment_open():
    from infra.database.models import ProductTaxRuleAssignmentModel
    from infra.repositories.fiscal_repo import SQLAlchemyProductTaxRuleRepository

    rule = published_st_rule()
    existing = ProductTaxRuleAssignmentModel(
        market_id=MARKET_ID,
        product_id=PRODUCT_ID,
        tax_rule_id=rule.id,
        effective_from=date.today(),
        effective_to=None,
    )
    session = AssignmentSession(existing_assignment=existing)
    repository = SQLAlchemyProductTaxRuleRepository(session)

    updated, skipped, _audit = await repository.assign_published_rule(
        market_id=MARKET_ID,
        product_ids=[PRODUCT_ID],
        rule=rule,
        effective_from=date.today(),
        actor_id=ACTOR_ID,
        reason="Reprocessamento idempotente",
    )

    assert updated == []
    assert skipped == [{"product_id": str(PRODUCT_ID), "reason": "already_assigned"}]
    assert existing.effective_to is None
    assert session.added == []


@pytest.mark.asyncio
async def test_production_repository_never_inverts_same_day_prior_assignment():
    from infra.database.models import ProductTaxRuleAssignmentModel
    from infra.repositories.fiscal_repo import SQLAlchemyProductTaxRuleRepository

    prior_rule = published_st_rule()
    replacement = published_st_rule()
    existing = ProductTaxRuleAssignmentModel(
        market_id=MARKET_ID,
        product_id=PRODUCT_ID,
        tax_rule_id=prior_rule.id,
        effective_from=date.today(),
        effective_to=None,
    )
    session = AssignmentSession(existing_assignment=existing)

    updated, skipped, _audit = await SQLAlchemyProductTaxRuleRepository(session).assign_published_rule(
        market_id=MARKET_ID,
        product_ids=[PRODUCT_ID],
        rule=replacement,
        effective_from=date.today(),
        actor_id=ACTOR_ID,
        reason="Correção fiscal aprovada",
    )

    assert updated == []
    assert skipped == [{"product_id": str(PRODUCT_ID), "reason": "same_day_reassignment"}]
    assert existing.effective_to is None
    assert session.added == []
