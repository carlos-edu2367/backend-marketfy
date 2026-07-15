"""Public HTTP/DTO contract for product-tax rule v2 APIs."""
from __future__ import annotations

import sys
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError


APP_DIR = Path(__file__).resolve().parents[2] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.append(str(APP_DIR))


MARKET_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
PRODUCT_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture(autouse=True)
def _test_settings(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("SECRET_KEY", "task-7-test-secret")
    from infra.config.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _draft_payload() -> dict:
    return {
        "name": "Refrigerante ST",
        "effective_from": "2026-07-15",
        "issuer_regime": "simples_nacional",
        "destination_uf": "GO",
        "document_model": "65",
        "ncm": "22021000",
        "cest": "0300700",
        "origin": "0",
        "cfop": "5405",
        "icms": {
            "group": "ICMSSN500",
            "cst": None,
            "csosn": "500",
            "mode": "retained_st",
            "retained_st_base": "140.00",
            "retained_st_rate": "18.0000",
            "retained_st_amount": "25.20",
            "retained_fcp_base": None,
            "retained_fcp_rate": None,
            "retained_fcp_amount": None,
        },
        "pis": {
            "group": "PIS07",
            "cst": "07",
            "base": "100.00",
            "rate": "0.0000",
            "amount": "0.00",
        },
        "cofins": {
            "group": "COFINS07",
            "cst": "07",
            "base": "100.00",
            "rate": "0.0000",
            "amount": "0.00",
        },
        "approval": {
            "reference": "Decreto GO 10.734/2025",
            "checksum": "a" * 64,
        },
    }


def test_exact_v2_routes_are_registered_once() -> None:
    from infra.web.main import app

    expected = {
        ("GET", "/api/v1/fiscal/{market_id}/tax-rules"),
        ("POST", "/api/v1/fiscal/{market_id}/tax-rules"),
        ("PATCH", "/api/v1/fiscal/{market_id}/tax-rules/{rule_id}/draft"),
        ("POST", "/api/v1/fiscal/{market_id}/tax-rules/{rule_id}/publish"),
        ("POST", "/api/v1/fiscal/{market_id}/tax-rules/{rule_id}/retire"),
        ("POST", "/api/v1/fiscal/{market_id}/tax-rule-assignments"),
        ("GET", "/api/v1/fiscal/{market_id}/tax-rule-pendencies"),
        ("POST", "/api/v1/fiscal/{market_id}/sales/preflight"),
        ("PATCH", "/api/v1/fiscal/{market_id}/product-rule-enforcement"),
    }
    registered = [
        (method, route.path)
        for route in app.routes
        for method in getattr(route, "methods", set())
        if (method, route.path) in expected
    ]

    assert set(registered) == expected
    assert len(registered) == len(expected)


def test_draft_dto_maps_explicit_nested_tax_parameters_to_domain() -> None:
    from application.fiscal_tax_dtos import FiscalTaxRuleDraftRequest

    dto = FiscalTaxRuleDraftRequest.model_validate(_draft_payload())
    values = dto.to_domain_kwargs()

    assert values["icms_group"] == "ICMSSN500"
    assert values["icms_csosn"] == "500"
    assert values["issuer_regime"].value == "simples_nacional"
    assert values["tax_parameters"]["icms_mode"] == "retained_st"
    assert values["tax_parameters"]["retained_st_amount"] == "25.20"
    assert values["tax_parameters"]["pis"]["rate"] == "0.0000"
    assert values["tax_parameters"]["cofins"]["amount"] == "0.00"
    assert values["approval"]["checksum"] == "a" * 64


def test_canonical_tax_rule_lifecycle_uses_the_strict_draft_contract() -> None:
    from inspect import signature
    from typing import get_type_hints

    from application.fiscal_tax_dtos import FiscalTaxRuleDraftRequest
    from infra.web.routers.fiscal import create_tax_rule_draft

    assert "dto" in signature(create_tax_rule_draft).parameters
    assert get_type_hints(create_tax_rule_draft)["dto"] is FiscalTaxRuleDraftRequest


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda body: body.update({"unexpected": True}), id="top-level-extra"),
        pytest.param(
            lambda body: body["icms"].update({"invented_rate": "1.00"}),
            id="nested-extra",
        ),
        pytest.param(lambda body: body["pis"].update({"rate": 1.65}), id="binary-float"),
    ],
)
def test_draft_dto_forbids_unknown_fields_and_binary_floats(mutate) -> None:
    from application.fiscal_tax_dtos import FiscalTaxRuleDraftRequest

    body = _draft_payload()
    mutate(body)

    with pytest.raises(ValidationError):
        FiscalTaxRuleDraftRequest.model_validate(body)


def test_preflight_dto_is_strict_and_rejects_float_quantity() -> None:
    from application.fiscal_tax_dtos import FiscalPreflightRequest

    valid = {
        "occurred_at": "2026-07-15T12:00:00Z",
        "items": [{"product_id": str(PRODUCT_ID), "quantity": "2.0000"}],
    }
    parsed = FiscalPreflightRequest.model_validate(valid)
    assert parsed.occurred_at == datetime(2026, 7, 15, 12, tzinfo=timezone.utc)

    valid["items"][0]["quantity"] = 2.0
    with pytest.raises(ValidationError):
        FiscalPreflightRequest.model_validate(valid)


@pytest.mark.asyncio
async def test_legacy_tax_profile_post_is_structured_gone() -> None:
    from infra.web.routers.fiscal import create_tax_profile

    with pytest.raises(HTTPException) as error:
        await create_tax_profile(
            request=None,
            market_id=MARKET_ID,
            name="Legado",
            ncm=None,
            cfop="5102",
            icms_csosn="102",
            pis_cst="07",
            cofins_cst="07",
            db=None,
            current_user=None,
            market=object(),
        )

    assert error.value.status_code == 410
    assert error.value.detail == {
        "code": "tax_profile.legacy_read_only",
        "message": "Perfis fiscais legados são somente leitura.",
    }


def test_assignment_and_enforcement_dtos_are_strict() -> None:
    from application.fiscal_tax_dtos import (
        FiscalRuleEnforcementRequest,
        ProductTaxRuleAssignmentRequest,
    )

    assignment = ProductTaxRuleAssignmentRequest.model_validate(
        {
            "tax_rule_id": str(uuid.uuid4()),
            "product_ids": [str(PRODUCT_ID)],
            "effective_from": date.today().isoformat(),
            "reason": "Revisão fiscal aprovada",
        }
    )
    assert assignment.product_ids == [PRODUCT_ID]

    with pytest.raises(ValidationError):
        FiscalRuleEnforcementRequest.model_validate(
            {"mode": "warn", "bypass_readiness": True}
        )


def test_cashier_has_no_fiscal_permission_but_manager_can_review() -> None:
    from domain.identity import UserRole
    from infra.security.market_access import (
        MarketPermission,
        role_has_permission,
    )

    assert not role_has_permission(UserRole.CASHIER, MarketPermission.FISCAL_READ)
    assert not role_has_permission(UserRole.CASHIER, MarketPermission.FISCAL_WRITE)
    assert role_has_permission(UserRole.MANAGER, MarketPermission.FISCAL_WRITE)


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        pytest.param("owner", True, id="owner"),
        pytest.param("manager", True, id="manager"),
        pytest.param("accountant", False, id="accountant"),
        pytest.param("cashier", False, id="cashier"),
    ],
)
def test_enforcement_gate_allows_only_owner_or_manager(role: str, allowed: bool) -> None:
    from domain.identity import UserRole
    from infra.web.routers.fiscal_tax_rules import assert_enforcement_role

    owner_id = uuid.uuid4()
    user_id = owner_id if role == "owner" else uuid.uuid4()
    market = SimpleNamespace(owner_id=owner_id)
    user = SimpleNamespace(id=user_id, role=UserRole(role))

    if allowed:
        assert assert_enforcement_role(current_user=user, market=market) is market
    else:
        with pytest.raises(HTTPException) as error:
            assert_enforcement_role(current_user=user, market=market)
        assert error.value.status_code == 403
        assert error.value.detail["code"] == "fiscal.rule_enforcement_forbidden"


def test_rollout_context_uses_tenant_data_and_rejects_unsupported_context() -> None:
    from application.services.fiscal.fiscal_rollout_service import (
        FiscalRolloutTransitionError,
    )
    from infra.web.routers.fiscal_tax_rules import rollout_context_from_tenant_config

    supported = SimpleNamespace(address_json={"uf": "GO"}, nfce_series=1)
    assert rollout_context_from_tenant_config(supported) == ("GO", "65")

    unsupported = SimpleNamespace(
        address_json={"uf": "SP"}, nfce_series=1, document_model="55"
    )
    with pytest.raises(FiscalRolloutTransitionError) as error:
        rollout_context_from_tenant_config(unsupported)
    assert error.value.code == "fiscal.rule_enforcement_context_unsupported"


@pytest.mark.asyncio
async def test_preflight_aggregates_every_product_error_without_writes() -> None:
    from application.services.fiscal.tax_rule_service import (
        FiscalRuleAmbiguousError,
        TaxRuleNotFoundError,
    )
    from application.services.sales_service import SalesService
    from domain.fiscal import FiscalRuleEnforcement

    missing_id = uuid.uuid4()
    ambiguous_id = uuid.uuid4()
    products = {
        missing_id: SimpleNamespace(
            id=missing_id,
            market_id=MARKET_ID,
            name="Sem regra",
            price=Decimal("10.00"),
        ),
        ambiguous_id: SimpleNamespace(
            id=ambiguous_id,
            market_id=MARKET_ID,
            name="Ambíguo",
            price=Decimal("20.00"),
        ),
    }

    class ProductReadsOnly:
        async def get_by_id(self, product_id):
            return products[product_id]

        async def save(self, *_args, **_kwargs):
            raise AssertionError("preflight must not write products")

    class ConfigReadsOnly:
        async def get_by_market(self, market_id):
            assert market_id == MARKET_ID
            return SimpleNamespace(
                fiscal_rule_enforcement=FiscalRuleEnforcement.BLOCK
            )

        async def save(self, *_args, **_kwargs):
            raise AssertionError("preflight must not write config")

    class Resolver:
        async def resolve_for_sale_item(self, *, product_id, **_kwargs):
            if product_id == missing_id:
                raise TaxRuleNotFoundError("missing")
            raise FiscalRuleAmbiguousError(product_id, date(2026, 7, 15))

    write_guard = SimpleNamespace(
        save=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("preflight must not write")
        )
    )
    service = SalesService(
        sale_repo=write_guard,
        box_repo=write_guard,
        product_repo=ProductReadsOnly(),
        market_repo=write_guard,
        terminal_repo=write_guard,
        user_repo=write_guard,
        plan_repo=write_guard,
        financial_repo=write_guard,
        fiscal_config_repo=ConfigReadsOnly(),
        tax_rule_service=Resolver(),
    )

    enforcement, errors = await service.fiscal_preflight(
        market_id=MARKET_ID,
        occurred_at=datetime(2026, 7, 15, 12, tzinfo=timezone.utc),
        items=[
            SimpleNamespace(product_id=missing_id, quantity=Decimal("1")),
            SimpleNamespace(product_id=ambiguous_id, quantity=Decimal("2")),
        ],
    )

    assert enforcement is FiscalRuleEnforcement.BLOCK
    assert [error["code"] for error in errors] == [
        "sale.fiscal_rule_missing",
        "sale.fiscal_rule_ambiguous",
    ]
    assert {error["product_id"] for error in errors} == {
        str(missing_id),
        str(ambiguous_id),
    }


@pytest.mark.asyncio
async def test_preflight_off_validates_every_item_without_writes() -> None:
    from application.services.fiscal.tax_rule_service import TaxRuleNotFoundError
    from application.services.sales_service import SalesService
    from domain.fiscal import FiscalRuleEnforcement

    known_id = uuid.uuid4()
    unknown_id = uuid.uuid4()
    product = SimpleNamespace(
        id=known_id,
        market_id=MARKET_ID,
        name="Sem regra",
        price=Decimal("10.00"),
    )

    class ProductReadsOnly:
        async def get_by_id(self, product_id):
            return product if product_id == known_id else None

        async def save(self, *_args, **_kwargs):
            raise AssertionError("preflight must not write products")

    class ConfigReadsOnly:
        async def get_by_market(self, _market_id):
            return SimpleNamespace(fiscal_rule_enforcement=FiscalRuleEnforcement.OFF)

        async def save(self, *_args, **_kwargs):
            raise AssertionError("preflight must not write config")

    class Resolver:
        async def resolve_for_sale_item(self, *, product_id, **_kwargs):
            assert product_id == known_id
            raise TaxRuleNotFoundError("missing")

    write_guard = SimpleNamespace(
        save=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("preflight must not write")
        )
    )
    service = SalesService(
        sale_repo=write_guard,
        box_repo=write_guard,
        product_repo=ProductReadsOnly(),
        market_repo=write_guard,
        terminal_repo=write_guard,
        user_repo=write_guard,
        plan_repo=write_guard,
        financial_repo=write_guard,
        fiscal_config_repo=ConfigReadsOnly(),
        tax_rule_service=Resolver(),
    )

    enforcement, errors = await service.fiscal_preflight(
        market_id=MARKET_ID,
        occurred_at=datetime(2026, 7, 15, 12, tzinfo=timezone.utc),
        items=[
            SimpleNamespace(product_id=unknown_id, quantity=Decimal("1")),
            SimpleNamespace(product_id=known_id, quantity=Decimal("1")),
        ],
    )

    assert enforcement is FiscalRuleEnforcement.OFF
    assert [error["code"] for error in errors] == [
        "sale.product_not_found",
        "sale.fiscal_rule_missing",
    ]
