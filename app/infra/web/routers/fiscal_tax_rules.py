"""Focused v2 fiscal-rule routes missing from the legacy fiscal router."""
from __future__ import annotations

import uuid
import json
from datetime import date
from functools import partial

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from application.fiscal_tax_dtos import (
    FiscalPreflightRequest,
    FiscalPreflightResponse,
    FiscalRuleEnforcementRequest,
    ProductTaxRuleAssignmentRequest,
)
from application.services.audit_service import AuditService
from application.services.fiscal.fiscal_rollout_service import (
    FiscalRolloutService,
    FiscalRolloutTransitionError,
)
from application.services.fiscal.tax_rule_service import TaxRuleService
from application.services.sales_service import SalesService
from domain.fiscal import FiscalRuleError
from domain.identity import User, UserRole
from infra.database.setup import get_db
from infra.observability.audit import record_audit_event
from infra.security.market_access import MarketPermission
from infra.web.dependencies import (
    get_audit_service,
    get_current_user,
    get_sales_service,
    require_market_access,
)


router = APIRouter()


def _tax_rule_service(db: AsyncSession) -> TaxRuleService:
    from infra.repositories.fiscal_repo import SQLAlchemyProductTaxRuleRepository

    return TaxRuleService(SQLAlchemyProductTaxRuleRepository(db))


def assert_enforcement_role(*, current_user: User, market):
    """Restrict fiscal rollout changes to the market owner or a manager."""
    if market.owner_id == current_user.id or current_user.role is UserRole.MANAGER:
        return market
    raise HTTPException(
        status_code=403,
        detail={
            "code": "fiscal.rule_enforcement_forbidden",
            "message": "Apenas o proprietário ou gerente pode alterar o enforcement fiscal.",
        },
    )


async def require_enforcement_access(
    current_user: User = Depends(get_current_user),
    market=Depends(require_market_access(MarketPermission.FISCAL_WRITE)),
):
    return assert_enforcement_role(current_user=current_user, market=market)


def rollout_context_from_tenant_config(config) -> tuple[str, str]:
    """Read the tenant's real NFC-e context and fail closed outside the rollout."""
    address = getattr(config, "address_json", None)
    if isinstance(address, str):
        try:
            address = json.loads(address)
        except (TypeError, ValueError):
            address = None
    address = address if isinstance(address, dict) else {}
    destination_uf = address.get("uf")
    document_model = getattr(config, "document_model", None)
    if document_model is None and getattr(config, "nfce_series", None):
        document_model = "65"

    if destination_uf != "GO" or document_model != "65":
        raise FiscalRolloutTransitionError(
            "O rollout de regras fiscais suporta apenas Goiás/NFC-e modelo 65.",
            code="fiscal.rule_enforcement_context_unsupported",
        )
    return destination_uf, document_model


async def assign_tax_rule_products(
    *,
    request: Request,
    market_id: uuid.UUID,
    dto: ProductTaxRuleAssignmentRequest,
    db: AsyncSession,
    current_user: User,
    audit: AuditService,
) -> dict:
    """Single HTTP adapter shared by the canonical route and inventory alias."""
    try:
        result = await _tax_rule_service(db).assign_products(
            market_id=market_id,
            rule_id=dto.tax_rule_id,
            product_ids=dto.product_ids,
            effective_from=dto.effective_from,
            actor_id=current_user.id,
            reason=dto.reason,
        )
    except FiscalRuleError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": exc.code,
                "message": str(exc),
                "items": exc.items,
            },
        ) from exc
    await record_audit_event(
        audit,
        request,
        actor=current_user,
        action="fiscal.product_tax_rule.bulk_assigned",
        resource_type="product_tax_rule_assignment",
        result="success",
        market_id=market_id,
        resource_id=str(dto.tax_rule_id),
        metadata={
            "reason": dto.reason.strip(),
            "effective_from": dto.effective_from.isoformat(),
            "product_ids": [str(product_id) for product_id in dto.product_ids],
            "before_rule_ids": result.previous_rule_ids,
            "after_rule_id": str(dto.tax_rule_id),
            "skipped": result.skipped,
        },
    )
    return {
        "updated_product_ids": [
            str(product_id) for product_id in result.updated_product_ids
        ],
        "skipped": result.skipped,
    }


@router.post("/{market_id}/tax-rule-assignments", tags=["fiscal"])
async def create_tax_rule_assignments(
    request: Request,
    market_id: uuid.UUID,
    dto: ProductTaxRuleAssignmentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    audit: AuditService = Depends(get_audit_service),
    market=Depends(require_market_access(MarketPermission.FISCAL_WRITE)),
):
    return await assign_tax_rule_products(
        request=request,
        market_id=market_id,
        dto=dto,
        db=db,
        current_user=current_user,
        audit=audit,
    )


@router.post(
    "/{market_id}/sales/preflight",
    response_model=FiscalPreflightResponse,
    tags=["fiscal"],
)
async def preflight_sale_fiscal_rules(
    market_id: uuid.UUID,
    dto: FiscalPreflightRequest,
    service: SalesService = Depends(get_sales_service),
    market=Depends(require_market_access(MarketPermission.FISCAL_READ)),
):
    enforcement, errors = await service.fiscal_preflight(
        market_id=market_id,
        occurred_at=dto.occurred_at,
        items=dto.items,
    )
    return {
        "allowed": enforcement.value != "block" or not errors,
        "enforcement": enforcement.value,
        "errors": errors,
    }


async def _market_is_ready_for_enforcement(
    market_id: uuid.UUID,
    *,
    db: AsyncSession,
) -> bool:
    from infra.repositories.fiscal_repo import SQLAlchemyFiscalTenantConfigRepository
    from infra.repositories.sqlalchemy_repos import SQLAlchemyProductRepository

    config = await SQLAlchemyFiscalTenantConfigRepository(db).get_by_market(market_id)
    if config is None or config.tax_regime is None:
        return False
    destination_uf, document_model = rollout_context_from_tenant_config(config)
    products = await SQLAlchemyProductRepository(db).list_by_market(market_id)
    report = await _tax_rule_service(db).list_pendencies(
        market_id=market_id,
        products=products,
        when=date.today(),
        issuer_regime=config.tax_regime,
        destination_uf=destination_uf,
        document_model=document_model,
    )
    return report.summary["configured"] == report.summary["total"]


@router.patch("/{market_id}/product-rule-enforcement", tags=["fiscal"])
async def update_product_rule_enforcement(
    request: Request,
    market_id: uuid.UUID,
    dto: FiscalRuleEnforcementRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    audit: AuditService = Depends(get_audit_service),
    market=Depends(require_enforcement_access),
):
    from infra.repositories.fiscal_repo import SQLAlchemyFiscalTenantConfigRepository

    repository = SQLAlchemyFiscalTenantConfigRepository(db)
    config = await repository.get_by_market(market_id)
    previous = config.fiscal_rule_enforcement.value if config is not None else None
    service = FiscalRolloutService(
        repository,
        readiness_provider=partial(_market_is_ready_for_enforcement, db=db),
    )
    try:
        mode = await service.transition(market_id=market_id, requested=dto.mode)
    except FiscalRolloutTransitionError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc

    await record_audit_event(
        audit,
        request,
        actor=current_user,
        action="fiscal.product_rule_enforcement.changed",
        resource_type="fiscal_tenant_config",
        resource_id=str(market_id),
        result="success",
        market_id=market_id,
        metadata={"previous_mode": previous, "new_mode": mode.value},
    )
    return {
        "market_id": str(market_id),
        "previous_mode": previous,
        "mode": mode.value,
    }
