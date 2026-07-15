"""
SQLAlchemy fiscal repository — implementações completas para os novos modelos.

Mantém SQLAlchemyFiscalRepository legado inalterado em sqlalchemy_repos.py.
Toda nova funcionalidade fiscal usa SQLAlchemyFiscalDocumentRepository aqui.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from copy import deepcopy
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

from sqlalchemy import and_, desc, func, or_, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from application.services.fiscal.tax_rule_service import (
    ProductTaxRuleAssociation,
    ProductTaxRuleCandidate,
    TaxRuleService,
)
from domain.shared import BusinessRuleException
from domain.interfaces import ProductTaxRuleRepositoryInterface

from domain.fiscal import (
    FiscalArtifact,
    FiscalArtifactType,
    FiscalAttempt,
    FiscalAttemptOperation,
    FiscalAttemptStatus,
    FiscalDocument,
    FiscalDocumentStatus,
    FiscalEmissionPackage,
    FiscalEnvironment,
    FiscalRuleEnforcement,
    FiscalRuleError,
    FiscalEvent,
    FiscalEventSource,
    FiscalNotification,
    FiscalNumberInutilizacao,
    ProviderWebhookEvent,
    FiscalTenantConfig,
    FiscalUsageCounter,
    FiscalUsageLedger,
    NotificationSeverity,
    NotificationType,
    NumberingMode,
    ProductTaxProfile,
    ProductTaxRule,
    ProductTaxRuleStatus,
    TaxRuleApproval,
    TaxRuleSefazAuthorization,
    TaxRegime,
    UsageLedgerEventType,
    ValidationStatus,
)
from infra.database.models import (
    FiscalArtifactModel,
    FiscalAttemptModel,
    FiscalDocumentModel,
    FiscalEmissionPackageModel,
    FiscalEventModel,
    FiscalInutilizacaoModel,
    FiscalNotificationModel,
    ProviderWebhookEventModel,
    FiscalTenantConfigModel,
    FiscalUsageCounterModel,
    FiscalUsageLedgerModel,
    ProductTaxProfileModel,
    ProductTaxRuleModel,
    TaxRuleApprovalModel,
    TaxRuleSefazAuthorizationModel,
    ProductTaxRuleAssignmentModel,
    ProductModel,
)


def _to_native_json(value):
    """Detach frozen domain evidence into JSON-native mutable containers."""
    if isinstance(value, Mapping):
        return {key: _to_native_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_native_json(item) for item in value]
    return deepcopy(value)


# =============================================================================
# FISCAL TENANT CONFIG
# =============================================================================

class SQLAlchemyFiscalTenantConfigRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_market(self, market_id: uuid.UUID) -> Optional[FiscalTenantConfig]:
        q = select(FiscalTenantConfigModel).where(FiscalTenantConfigModel.market_id == market_id)
        r = await self.session.execute(q)
        m = r.scalars().first()
        return _to_tenant_config(m) if m else None

    async def save(self, cfg: FiscalTenantConfig, commit: bool = True) -> FiscalTenantConfig:
        m = await self.session.get(FiscalTenantConfigModel, cfg.id)
        if not m:
            m = FiscalTenantConfigModel(id=cfg.id)
            self.session.add(m)

        m.market_id = cfg.market_id
        m.provider = cfg.provider
        m.environment = cfg.environment.value
        m.enabled = cfg.enabled
        m.fiscal_rule_enforcement = cfg.fiscal_rule_enforcement.value
        m.legal_name = cfg.legal_name
        m.trade_name = cfg.trade_name
        m.cnpj = cfg.cnpj
        m.state_registration = cfg.state_registration
        m.municipal_registration = cfg.municipal_registration
        m.tax_regime = cfg.tax_regime.value if cfg.tax_regime else None
        m.crt = cfg.crt
        m.cnae_primary = cfg.cnae_primary
        m.address_json = json.dumps(cfg.address_json) if cfg.address_json else None
        m.certificate_storage_key = cfg.certificate_storage_key
        m.certificate_password_ciphertext = cfg.certificate_password_ciphertext
        m.certificate_fingerprint = cfg.certificate_fingerprint
        m.certificate_valid_until = cfg.certificate_valid_until
        m.csc_id_ciphertext = cfg.csc_id_ciphertext
        m.csc_token_ciphertext = cfg.csc_token_ciphertext
        m.nfce_series = cfg.nfce_series
        m.nfce_next_number = cfg.nfce_next_number
        m.numbering_mode = cfg.numbering_mode.value
        m.contingency_series = cfg.contingency_series
        m.contingency_next_number = cfg.contingency_next_number
        m.default_cfop = cfg.default_cfop
        m.default_ncm = cfg.default_ncm
        m.default_csosn = cfg.default_csosn
        m.default_cst = cfg.default_cst
        m.responsavel_tecnico = json.dumps(cfg.responsavel_tecnico) if cfg.responsavel_tecnico else None
        m.validation_status = cfg.validation_status.value
        m.last_validated_at = cfg.last_validated_at

        if commit:
            await self.session.commit()
            await self.session.refresh(m)
        cfg.updated_at = m.updated_at
        return cfg

    async def update_neectify_fields(
        self,
        market_id: uuid.UUID,
        issuer_id: Optional[str] = None,
        cert_id: Optional[str] = None,
        config_id: Optional[str] = None,
        onboarding_step: Optional[str] = None,
        commit: bool = True,
    ) -> None:
        """Atualiza campos Neectify sem tocar no restante da config fiscal."""
        values: dict = {"updated_at": datetime.utcnow()}
        if issuer_id is not None:
            values["neectify_issuer_id"] = issuer_id
        if cert_id is not None:
            values["neectify_cert_id"] = cert_id
        if config_id is not None:
            values["neectify_config_id"] = config_id
        if onboarding_step is not None:
            values["neectify_onboarding_step"] = onboarding_step

        stmt = (
            update(FiscalTenantConfigModel)
            .where(FiscalTenantConfigModel.market_id == market_id)
            .values(**values)
        )
        await self.session.execute(stmt)
        if commit:
            await self.session.commit()


def _to_tenant_config(m: FiscalTenantConfigModel) -> FiscalTenantConfig:
    cfg = FiscalTenantConfig(
        market_id=m.market_id,
        provider=m.provider,
        environment=FiscalEnvironment(m.environment),
        enabled=m.enabled,
        fiscal_rule_enforcement=FiscalRuleEnforcement(m.fiscal_rule_enforcement),
        legal_name=m.legal_name,
        trade_name=m.trade_name,
        cnpj=m.cnpj,
        state_registration=m.state_registration,
        municipal_registration=m.municipal_registration,
        tax_regime=TaxRegime(m.tax_regime) if m.tax_regime else None,
        crt=m.crt,
        cnae_primary=m.cnae_primary,
        address_json=json.loads(m.address_json) if m.address_json else None,
        certificate_storage_key=m.certificate_storage_key,
        certificate_password_ciphertext=m.certificate_password_ciphertext,
        certificate_fingerprint=m.certificate_fingerprint,
        certificate_valid_until=m.certificate_valid_until,
        csc_id_ciphertext=m.csc_id_ciphertext,
        csc_token_ciphertext=m.csc_token_ciphertext,
        nfce_series=m.nfce_series,
        nfce_next_number=m.nfce_next_number,
        numbering_mode=NumberingMode(m.numbering_mode),
        contingency_series=m.contingency_series,
        contingency_next_number=m.contingency_next_number,
        default_cfop=m.default_cfop,
        default_ncm=m.default_ncm,
        default_csosn=m.default_csosn,
        default_cst=m.default_cst,
        responsavel_tecnico=json.loads(m.responsavel_tecnico) if m.responsavel_tecnico else None,
        validation_status=ValidationStatus(m.validation_status),
        last_validated_at=m.last_validated_at,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )
    cfg.id = m.id
    # Campos Neectify (não estão na dataclass — expostos via setattr para acesso por getattr)
    cfg.neectify_issuer_id = getattr(m, "neectify_issuer_id", None)
    cfg.neectify_cert_id = getattr(m, "neectify_cert_id", None)
    cfg.neectify_config_id = getattr(m, "neectify_config_id", None)
    cfg.neectify_onboarding_step = getattr(m, "neectify_onboarding_step", "pending")
    return cfg


# =============================================================================
# PRODUCT TAX PROFILE
# =============================================================================

class SQLAlchemyProductTaxProfileRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, profile_id: uuid.UUID) -> Optional[ProductTaxProfile]:
        m = await self.session.get(ProductTaxProfileModel, profile_id)
        return _to_tax_profile(m) if m else None

    async def list_by_market(self, market_id: uuid.UUID, active_only: bool = True) -> List[ProductTaxProfile]:
        q = select(ProductTaxProfileModel).where(ProductTaxProfileModel.market_id == market_id)
        if active_only:
            q = q.where(ProductTaxProfileModel.active == True)
        q = q.order_by(ProductTaxProfileModel.name)
        r = await self.session.execute(q)
        return [_to_tax_profile(m) for m in r.scalars().all()]

    async def save(self, profile: ProductTaxProfile, commit: bool = True) -> ProductTaxProfile:
        m = await self.session.get(ProductTaxProfileModel, profile.id)
        if not m:
            m = ProductTaxProfileModel(id=profile.id)
            self.session.add(m)

        m.market_id = profile.market_id
        m.name = profile.name
        m.ncm = profile.ncm
        m.cest = profile.cest
        m.cfop = profile.cfop
        m.origin = profile.origin
        m.icms_cst = profile.icms_cst
        m.icms_csosn = profile.icms_csosn
        m.pis_cst = profile.pis_cst
        m.cofins_cst = profile.cofins_cst
        m.aliquotas_json = json.dumps(profile.aliquotas_json) if profile.aliquotas_json else None
        m.effective_from = profile.effective_from
        m.active = profile.active

        if commit:
            await self.session.commit()
        return profile


def _to_tax_profile(m: ProductTaxProfileModel) -> ProductTaxProfile:
    p = ProductTaxProfile(
        market_id=m.market_id,
        name=m.name,
        ncm=m.ncm,
        cest=m.cest,
        cfop=m.cfop,
        origin=m.origin,
        icms_cst=m.icms_cst,
        icms_csosn=m.icms_csosn,
        pis_cst=m.pis_cst,
        cofins_cst=m.cofins_cst,
        aliquotas_json=json.loads(m.aliquotas_json) if m.aliquotas_json else None,
        effective_from=m.effective_from,
        active=m.active,
    )
    p.id = m.id
    return p


class SQLAlchemyProductTaxRuleRepository(ProductTaxRuleRepositoryInterface):
    """Loads rules through the durable product-association timeline."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_draft(self, rule: ProductTaxRule) -> ProductTaxRule:
        if rule.status is not ProductTaxRuleStatus.DRAFT:
            raise BusinessRuleException("Uma nova regra fiscal deve iniciar como rascunho.")
        model = ProductTaxRuleModel(id=rule.id)
        self.session.add(model)
        self._copy_rule_to_model(rule, model)
        await self.session.commit()
        await self.session.refresh(model)
        return _to_product_tax_rule(model)

    async def get_rule(self, market_id: uuid.UUID, rule_id: uuid.UUID) -> Optional[ProductTaxRule]:
        model = await self.session.get(ProductTaxRuleModel, rule_id)
        if model is None or model.market_id != market_id:
            return None
        return _to_product_tax_rule(model)

    async def list_rules(self, market_id: uuid.UUID) -> List[ProductTaxRule]:
        result = await self.session.execute(
            select(ProductTaxRuleModel)
            .where(ProductTaxRuleModel.market_id == market_id)
            .order_by(desc(ProductTaxRuleModel.updated_at), ProductTaxRuleModel.name)
        )
        return [_to_product_tax_rule(model) for model in result.scalars().all()]

    async def update_draft(self, rule: ProductTaxRule, changes: dict) -> ProductTaxRule:
        model = await self.session.get(ProductTaxRuleModel, rule.id)
        if model is None or model.market_id != rule.market_id:
            raise BusinessRuleException("Regra fiscal não encontrada.")
        if model.status != ProductTaxRuleStatus.DRAFT.value:
            raise BusinessRuleException("Somente rascunhos fiscais podem ser alterados.")
        for field, value in changes.items():
            if hasattr(model, field):
                setattr(model, field, value)
        model.updated_at = datetime.utcnow()
        await self.session.commit()
        await self.session.refresh(model)
        return _to_product_tax_rule(model)

    async def publish_rule(self, rule: ProductTaxRule) -> ProductTaxRule:
        raise BusinessRuleException(
            "A publicação exige uma aprovação contábil persistida e XML homologado."
        )

    async def create_successor(self, rule: ProductTaxRule, *, effective_from: date) -> ProductTaxRule:
        """Persist the next immutable-family version as an editable draft."""
        model = await self.session.get(ProductTaxRuleModel, rule.id)
        if model is None or model.market_id != rule.market_id:
            raise BusinessRuleException("Regra fiscal não encontrada.")
        successor = _to_product_tax_rule(model).create_successor(effective_from=effective_from)
        return await self.create_draft(successor)

    async def publish_rule_with_approval(
        self, rule: ProductTaxRule, approval: TaxRuleApproval
    ) -> ProductTaxRule:
        model = await self.session.get(
            ProductTaxRuleModel, rule.id, with_for_update=True
        )
        if model is None or model.market_id != rule.market_id:
            raise FiscalRuleError("tax_rule.not_found", "Regra fiscal não encontrada.")
        if model.status != ProductTaxRuleStatus.DRAFT.value:
            raise FiscalRuleError(
                "tax_rule.not_draft",
                "Somente rascunhos fiscais podem ser publicados.",
            )

        if approval.rule_id != rule.id:
            raise FiscalRuleError(
                "tax_rule.evidence_required",
                "A evidência fiscal não pertence à regra.",
            )
        if model.updated_at != rule.updated_at:
            raise FiscalRuleError(
                "tax_rule.stale_review",
                "O rascunho mudou após a revisão; revise e capture nova evidência.",
            )

        canonical_rule = _to_product_tax_rule(model)
        self._validate_for_publication(canonical_rule)
        self.session.add(
            TaxRuleApprovalModel(
                rule_id=approval.rule_id,
                accountant_user_id=approval.accountant_user_id,
                homologation_xml_storage_key=approval.homologation_xml_storage_key,
                homologation_xml_sha256=approval.homologation_xml_sha256,
                approved_at=approval.approved_at,
            )
        )
        model.approved_by = approval.accountant_user_id
        model.approved_at = approval.approved_at
        model.status = ProductTaxRuleStatus.PUBLISHED.value
        model.updated_at = datetime.utcnow()
        await self.session.commit()
        await self.session.refresh(model)
        return _to_product_tax_rule(model)

    async def save_sefaz_authorization(
        self, authorization: TaxRuleSefazAuthorization
    ) -> TaxRuleSefazAuthorization:
        model = TaxRuleSefazAuthorizationModel(
            rule_id=authorization.rule_id,
            accountant_user_id=authorization.accountant_user_id,
            authorized_xml_storage_key=authorization.authorized_xml_storage_key,
            xml_sha256=authorization.xml_sha256,
            access_key=authorization.access_key,
            protocol=authorization.protocol,
            authorized_at=authorization.authorized_at,
            recorded_at=authorization.recorded_at,
        )
        self.session.add(model)
        await self.session.commit()
        return authorization

    async def get_sefaz_authorization(
        self, rule_id: uuid.UUID
    ) -> Optional[TaxRuleSefazAuthorization]:
        model = await self.session.get(TaxRuleSefazAuthorizationModel, rule_id)
        if model is None:
            return None
        return TaxRuleSefazAuthorization(
            rule_id=model.rule_id,
            accountant_user_id=model.accountant_user_id,
            authorized_xml_storage_key=model.authorized_xml_storage_key,
            xml_sha256=model.xml_sha256,
            access_key=model.access_key,
            protocol=model.protocol,
            authorized_at=model.authorized_at,
            recorded_at=model.recorded_at,
        )

    async def retire_rule(self, rule: ProductTaxRule) -> ProductTaxRule:
        model = await self.session.get(ProductTaxRuleModel, rule.id)
        if model is None or model.market_id != rule.market_id:
            raise BusinessRuleException("Regra fiscal não encontrada.")
        if model.status != ProductTaxRuleStatus.PUBLISHED.value:
            raise BusinessRuleException("Somente uma regra publicada pode ser retirada.")
        model.status = ProductTaxRuleStatus.RETIRED.value
        model.updated_at = datetime.utcnow()
        await self.session.commit()
        await self.session.refresh(model)
        return _to_product_tax_rule(model)

    async def assign_published_rule(
        self,
        *,
        market_id: uuid.UUID,
        product_ids: List[uuid.UUID],
        rule: ProductTaxRule,
        effective_from: date,
        actor_id: uuid.UUID,
        reason: str,
    ) -> Tuple[List[uuid.UUID], List[dict], List[dict]]:
        """Apply an explicit rule link without ever inferring fiscal data.

        Assignment is intentionally current-date only: the products table is a
        current pointer and this application has no future-link scheduler.
        Historical dates remain resolved by the durable assignment timeline.
        """
        if rule.status is not ProductTaxRuleStatus.PUBLISHED:
            raise FiscalRuleError(
                "tax_rule.not_published",
                "Somente regras fiscais publicadas podem ser atribuídas.",
            )
        if len(set(product_ids)) != len(product_ids):
            raise FiscalRuleError(
                "tax_rule.duplicate_product",
                "A associação em massa contém produto duplicado.",
            )
        if effective_from != date.today():
            raise FiscalRuleError(
                "tax_rule.assignment_date_invalid",
                "A associação fiscal deve vigorar na data atual.",
            )
        if not rule.is_effective_on(effective_from):
            raise FiscalRuleError(
                "tax_rule.rule_not_effective",
                "A regra fiscal publicada não está vigente para a associação.",
            )

        locked_rule = await self.session.get(
            ProductTaxRuleModel, rule.id, with_for_update=True
        )
        if locked_rule is None or locked_rule.market_id != market_id:
            raise FiscalRuleError("tax_rule.not_found", "Regra fiscal não encontrada.")
        if locked_rule.status != ProductTaxRuleStatus.PUBLISHED.value:
            raise FiscalRuleError(
                "tax_rule.not_published",
                "Somente regras fiscais publicadas podem ser atribuídas.",
            )

        products = (
            await self.session.execute(
                select(ProductModel).where(
                    ProductModel.market_id == market_id,
                    ProductModel.id.in_(product_ids),
                    ProductModel.deleted_at.is_(None),
                ).with_for_update()
            )
        ).scalars().all()
        products_by_id = {product.id: product for product in products}
        missing_product_ids = [
            product_id for product_id in product_ids if product_id not in products_by_id
        ]
        if missing_product_ids:
            raise FiscalRuleError(
                "tax_rule.product_market_mismatch",
                "Um ou mais produtos não existem no mercado informado.",
                [
                    {"product_id": str(product_id)}
                    for product_id in missing_product_ids
                ],
            )
        updated: List[uuid.UUID] = []
        skipped: List[dict] = []
        audit_changes: List[dict] = []

        for product_id in product_ids:
            product = products_by_id.get(product_id)

            assignments = (
                await self.session.execute(
                    select(ProductTaxRuleAssignmentModel)
                    .where(
                        ProductTaxRuleAssignmentModel.market_id == market_id,
                        ProductTaxRuleAssignmentModel.product_id == product_id,
                    )
                    .order_by(desc(ProductTaxRuleAssignmentModel.effective_from))
                    .with_for_update()
                )
            ).scalars().all()
            open_assignment = next((a for a in assignments if a.effective_to is None), None)
            conflicts = [
                assignment
                for assignment in assignments
                if assignment.effective_to is not None and assignment.effective_to >= effective_from
            ]
            if conflicts or (open_assignment is not None and open_assignment.effective_from > effective_from):
                skipped.append({"product_id": str(product_id), "reason": "overlap"})
                continue

            if open_assignment is not None and open_assignment.effective_from == effective_from:
                if open_assignment.tax_rule_id == rule.id:
                    skipped.append({"product_id": str(product_id), "reason": "already_assigned"})
                else:
                    skipped.append({"product_id": str(product_id), "reason": "same_day_reassignment"})
                continue

            before_rule_id = product.tax_rule_id
            if open_assignment is not None:
                open_assignment.effective_to = effective_from - timedelta(days=1)

            self.session.add(
                ProductTaxRuleAssignmentModel(
                    market_id=market_id,
                    product_id=product_id,
                    tax_rule_id=rule.id,
                    effective_from=effective_from,
                    effective_to=None,
                )
            )
            product.tax_rule_id = rule.id
            product.updated_at = datetime.utcnow()
            updated.append(product_id)
            audit_changes.append(
                {
                    "product_id": str(product_id),
                    "before_rule_id": str(before_rule_id) if before_rule_id else None,
                    "after_rule_id": str(rule.id),
                }
            )

        if updated:
            try:
                await self.session.commit()
            except (IntegrityError, OperationalError) as exc:
                await self.session.rollback()
                sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
                if (
                    "ex_product_tax_rule_assignment_effective_range" in str(exc)
                    or sqlstate == "40P01"
                ):
                    raise FiscalRuleError(
                        "tax_rule.assignment_conflict",
                        "Conflito de vigência: o produto já possui regra fiscal para este período.",
                    ) from exc
                raise
        return updated, skipped, audit_changes

    async def list_product_rule_associations(
        self, market_id: uuid.UUID, product_ids: List[uuid.UUID]
    ) -> dict[uuid.UUID, List[ProductTaxRuleAssociation]]:
        """Load raw tenant-scoped history; status classification belongs to the service."""
        associations: dict[uuid.UUID, List[ProductTaxRuleAssociation]] = {
            product_id: [] for product_id in product_ids
        }
        if not product_ids:
            return associations
        result = await self.session.execute(
            select(ProductTaxRuleAssignmentModel, ProductTaxRuleModel)
            .join(
                ProductTaxRuleModel,
                ProductTaxRuleAssignmentModel.tax_rule_id == ProductTaxRuleModel.id,
            )
            .where(
                ProductTaxRuleAssignmentModel.market_id == market_id,
                ProductTaxRuleModel.market_id == market_id,
                ProductTaxRuleAssignmentModel.product_id.in_(product_ids),
            )
            .order_by(
                ProductTaxRuleAssignmentModel.product_id,
                ProductTaxRuleAssignmentModel.effective_from,
            )
        )
        for assignment, rule_model in result.all():
            associations.setdefault(assignment.product_id, []).append(
                ProductTaxRuleAssociation(
                    association_id=assignment.id,
                    effective_from=assignment.effective_from,
                    effective_to=assignment.effective_to,
                    rule=_to_product_tax_rule(rule_model),
                )
            )
        return associations

    async def list_product_fiscal_status(
        self, market_id: uuid.UUID, product_ids: List[uuid.UUID], when: date
    ) -> dict[uuid.UUID, str]:
        result = await self.session.execute(
            select(ProductTaxRuleAssignmentModel, ProductTaxRuleModel)
            .join(
                ProductTaxRuleModel,
                ProductTaxRuleAssignmentModel.tax_rule_id == ProductTaxRuleModel.id,
            )
            .where(
                ProductTaxRuleAssignmentModel.market_id == market_id,
                ProductTaxRuleAssignmentModel.product_id.in_(product_ids),
            )
        )
        matches: dict[uuid.UUID, List[ProductTaxRuleModel]] = {product_id: [] for product_id in product_ids}
        history_present: set[uuid.UUID] = set()
        for assignment, rule in result.all():
            history_present.add(assignment.product_id)
            if assignment.effective_from <= when and (
                assignment.effective_to is None or assignment.effective_to >= when
            ):
                matches.setdefault(assignment.product_id, []).append(rule)

        statuses: dict[uuid.UUID, str] = {}
        for product_id in product_ids:
            candidates = matches.get(product_id, [])
            if len(candidates) > 1:
                statuses[product_id] = "ambiguous"
            elif not candidates:
                statuses[product_id] = "expired" if product_id in history_present else "missing"
            elif candidates[0].status == ProductTaxRuleStatus.PUBLISHED.value:
                statuses[product_id] = "ready"
            else:
                statuses[product_id] = "draft"
        return statuses

    async def list_effective_linked_rules(
        self,
        market_id: uuid.UUID,
        product_id: uuid.UUID,
        occurred_on: date,
    ) -> List[ProductTaxRuleCandidate]:
        query = (
            select(ProductTaxRuleAssignmentModel.id, ProductTaxRuleModel)
            .join(
                ProductTaxRuleAssignmentModel,
                ProductTaxRuleAssignmentModel.tax_rule_id == ProductTaxRuleModel.id,
            )
            .where(
                ProductTaxRuleAssignmentModel.product_id == product_id,
                ProductTaxRuleAssignmentModel.market_id == market_id,
                ProductTaxRuleModel.market_id == market_id,
                ProductTaxRuleAssignmentModel.effective_from <= occurred_on,
                (
                    ProductTaxRuleAssignmentModel.effective_to.is_(None)
                    | (ProductTaxRuleAssignmentModel.effective_to >= occurred_on)
                ),
            )
            .order_by(
                desc(ProductTaxRuleModel.effective_from),
                desc(ProductTaxRuleModel.id),
            )
        )
        result = await self.session.execute(query)
        return [
            ProductTaxRuleCandidate(association_id, _to_product_tax_rule(rule_model))
            for association_id, rule_model in result.all()
        ]

    async def get_effective_published_rule(
        self,
        *,
        market_id: uuid.UUID,
        product_id: uuid.UUID,
        on_date: date,
    ) -> Optional[ProductTaxRule]:
        """Resolve one published rule through the durable assignment history."""
        query = (
            select(ProductTaxRuleModel)
            .join(
                ProductTaxRuleAssignmentModel,
                ProductTaxRuleAssignmentModel.tax_rule_id == ProductTaxRuleModel.id,
            )
            .join(
                ProductModel,
                ProductModel.id == ProductTaxRuleAssignmentModel.product_id,
            )
            .where(
                ProductModel.id == product_id,
                ProductModel.market_id == market_id,
                ProductTaxRuleAssignmentModel.product_id == product_id,
                ProductTaxRuleAssignmentModel.market_id == market_id,
                ProductTaxRuleModel.market_id == market_id,
                ProductTaxRuleModel.status == ProductTaxRuleStatus.PUBLISHED.value,
                ProductTaxRuleAssignmentModel.effective_from <= on_date,
                or_(
                    ProductTaxRuleAssignmentModel.effective_to.is_(None),
                    ProductTaxRuleAssignmentModel.effective_to >= on_date,
                ),
                ProductTaxRuleModel.effective_from <= on_date,
                or_(
                    ProductTaxRuleModel.effective_to.is_(None),
                    ProductTaxRuleModel.effective_to >= on_date,
                ),
            )
            .with_for_update(read=True)
        )
        rows = (await self.session.execute(query)).scalars().all()
        if len(rows) > 1:
            raise BusinessRuleException("Mais de uma regra fiscal vigente para o produto.")
        return _to_product_tax_rule(rows[0]) if rows else None

    @staticmethod
    def _copy_rule_to_model(rule: ProductTaxRule, model: ProductTaxRuleModel) -> None:
        for field in (
            "market_id", "name", "rule_family_id", "supersedes_rule_id", "effective_from", "effective_to",
            "issuer_regime", "destination_uf", "document_model", "ncm", "cest", "origin", "cfop", "cbenef",
            "icms_group", "icms_cst", "icms_csosn", "icms_mod_bc", "icms_rate", "icms_reduction_rate",
            "icms_st_mod_bc", "icms_st_mva_rate", "icms_st_rate", "fcp_rate", "pis_cst", "pis_rate",
            "cofins_cst", "cofins_rate", "approved_by", "approved_at",
        ):
            value = getattr(rule, field)
            if field == "issuer_regime" and value is not None:
                value = value.value
            setattr(model, field, value)
        model.tax_parameters_json = _to_native_json(rule.tax_parameters)
        model.approval_json = _to_native_json(rule.approval)

    @staticmethod
    def _validate_for_publication(rule: ProductTaxRule) -> None:
        TaxRuleService._validate_for_publication(rule)


def _to_product_tax_rule(model: ProductTaxRuleModel) -> ProductTaxRule:
    rule = ProductTaxRule(
        market_id=model.market_id,
        name=model.name,
        version=model.version,
        rule_family_id=model.rule_family_id,
        supersedes_rule_id=model.supersedes_rule_id,
        status=ProductTaxRuleStatus(model.status),
        effective_from=model.effective_from,
        effective_to=model.effective_to,
        issuer_regime=TaxRegime(model.issuer_regime) if model.issuer_regime else None,
        destination_uf=model.destination_uf,
        document_model=model.document_model,
        ncm=model.ncm,
        cest=model.cest,
        origin=model.origin,
        cfop=model.cfop,
        cbenef=model.cbenef,
        icms_group=model.icms_group,
        icms_cst=model.icms_cst,
        icms_csosn=model.icms_csosn,
        icms_mod_bc=model.icms_mod_bc,
        icms_rate=model.icms_rate,
        icms_reduction_rate=model.icms_reduction_rate,
        icms_st_mod_bc=model.icms_st_mod_bc,
        icms_st_mva_rate=model.icms_st_mva_rate,
        icms_st_rate=model.icms_st_rate,
        fcp_rate=model.fcp_rate,
        pis_cst=model.pis_cst,
        pis_rate=model.pis_rate,
        cofins_cst=model.cofins_cst,
        cofins_rate=model.cofins_rate,
        tax_parameters=model.tax_parameters_json,
        approval=model.approval_json,
        approved_by=model.approved_by,
        approved_at=model.approved_at,
    )
    rule.id = model.id
    rule.created_at = model.created_at
    rule.updated_at = model.updated_at
    return rule


# =============================================================================
# FISCAL DOCUMENT
# =============================================================================

class SQLAlchemyFiscalDocumentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, doc_id: uuid.UUID) -> Optional[FiscalDocument]:
        m = await self.session.get(FiscalDocumentModel, doc_id)
        return _to_document(m) if m else None

    async def get_by_sale(
        self, market_id: uuid.UUID, sale_id: uuid.UUID, document_type: str = "nfce"
    ) -> Optional[FiscalDocument]:
        q = select(FiscalDocumentModel).where(
            FiscalDocumentModel.market_id == market_id,
            FiscalDocumentModel.sale_id == sale_id,
            FiscalDocumentModel.document_type == document_type,
        )
        r = await self.session.execute(q)
        m = r.scalars().first()
        return _to_document(m) if m else None

    async def get_by_provider_ref(self, provider: str, provider_ref: str) -> Optional[FiscalDocument]:
        q = select(FiscalDocumentModel).where(
            FiscalDocumentModel.provider == provider,
            FiscalDocumentModel.provider_ref == provider_ref,
        )
        r = await self.session.execute(q)
        m = r.scalars().first()
        return _to_document(m) if m else None

    async def list_by_market(
        self,
        market_id: uuid.UUID,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[FiscalDocument], int]:
        q = select(FiscalDocumentModel).where(FiscalDocumentModel.market_id == market_id)
        if status:
            q = q.where(FiscalDocumentModel.status == status)
        count_q = select(func.count()).select_from(q.subquery())
        total = (await self.session.execute(count_q)).scalar() or 0
        q = q.order_by(desc(FiscalDocumentModel.created_at)).limit(limit).offset(offset)
        r = await self.session.execute(q)
        docs = [_to_document(m) for m in r.scalars().all()]
        return docs, total

    async def list_pending_retry(
        self, limit: int = 100, before: Optional[datetime] = None
    ) -> List[FiscalDocument]:
        """Documentos elegíveis para retry no worker."""
        now = before or datetime.utcnow()
        q = select(FiscalDocumentModel).where(
            FiscalDocumentModel.status.in_(["queued", "processing", "provider_error", "sefaz_unavailable"]),
        ).order_by(FiscalDocumentModel.created_at).limit(limit)
        r = await self.session.execute(q)
        return [_to_document(m) for m in r.scalars().all()]

    async def save(self, doc: FiscalDocument, commit: bool = True) -> FiscalDocument:
        m = await self.session.get(FiscalDocumentModel, doc.id)
        if not m:
            m = FiscalDocumentModel(id=doc.id)
            self.session.add(m)

        m.market_id = doc.market_id
        m.sale_id = doc.sale_id
        m.document_type = doc.document_type
        m.provider = doc.provider
        m.provider_ref = doc.provider_ref
        m.environment = doc.environment.value
        m.status = doc.status.value
        m.idempotency_key = doc.idempotency_key
        m.series = doc.series
        m.number = doc.number
        m.access_key = doc.access_key
        m.protocol = doc.protocol
        m.sefaz_status_code = doc.sefaz_status_code
        m.sefaz_message = doc.sefaz_message
        m.provider_status = doc.provider_status
        m.issued_at = doc.issued_at
        m.authorized_at = doc.authorized_at
        m.canceled_at = doc.canceled_at
        m.contingency_mode = doc.contingency_mode
        m.offline_receipt_id = doc.offline_receipt_id
        m.last_attempt_id = doc.last_attempt_id

        if commit:
            await self.session.commit()
            await self.session.refresh(m)
        doc.updated_at = m.updated_at
        return doc


def _to_document(m: FiscalDocumentModel) -> FiscalDocument:
    doc = FiscalDocument(
        market_id=m.market_id,
        sale_id=m.sale_id,
        document_type=m.document_type,
        provider=m.provider,
        provider_ref=m.provider_ref,
        environment=FiscalEnvironment(m.environment),
        status=FiscalDocumentStatus(m.status),
        idempotency_key=m.idempotency_key,
        series=m.series,
        number=m.number,
        access_key=m.access_key,
        protocol=m.protocol,
        sefaz_status_code=m.sefaz_status_code,
        sefaz_message=m.sefaz_message,
        provider_status=m.provider_status,
        issued_at=m.issued_at,
        authorized_at=m.authorized_at,
        canceled_at=m.canceled_at,
        contingency_mode=m.contingency_mode,
        offline_receipt_id=m.offline_receipt_id,
        last_attempt_id=m.last_attempt_id,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )
    doc.id = m.id
    return doc


# =============================================================================
# FISCAL ATTEMPT
# =============================================================================

class SQLAlchemyFiscalAttemptRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(self, attempt: FiscalAttempt, commit: bool = True) -> FiscalAttempt:
        m = await self.session.get(FiscalAttemptModel, attempt.id)
        if not m:
            m = FiscalAttemptModel(id=attempt.id)
            self.session.add(m)

        m.fiscal_document_id = attempt.fiscal_document_id
        m.market_id = attempt.market_id
        m.provider = attempt.provider
        m.operation = attempt.operation.value
        m.attempt_number = attempt.attempt_number
        m.status = attempt.status.value
        m.request_hash = attempt.request_hash
        m.provider_request_id = attempt.provider_request_id
        m.http_status = attempt.http_status
        m.error_code = attempt.error_code
        m.error_message = attempt.error_message
        m.started_at = attempt.started_at
        m.finished_at = attempt.finished_at
        m.duration_ms = attempt.duration_ms
        m.next_retry_at = attempt.next_retry_at

        if commit:
            await self.session.commit()
        return attempt

    async def count_attempts(self, doc_id: uuid.UUID, operation: str = "emit") -> int:
        q = select(func.count()).where(
            FiscalAttemptModel.fiscal_document_id == doc_id,
            FiscalAttemptModel.operation == operation,
        )
        r = await self.session.execute(q)
        return r.scalar() or 0

    async def list_by_document(self, doc_id: uuid.UUID) -> List[FiscalAttempt]:
        q = select(FiscalAttemptModel).where(
            FiscalAttemptModel.fiscal_document_id == doc_id
        ).order_by(FiscalAttemptModel.started_at)
        r = await self.session.execute(q)
        return [_to_attempt(m) for m in r.scalars().all()]


def _to_attempt(m: FiscalAttemptModel) -> FiscalAttempt:
    a = FiscalAttempt(
        fiscal_document_id=m.fiscal_document_id,
        market_id=m.market_id,
        provider=m.provider,
        operation=FiscalAttemptOperation(m.operation),
        attempt_number=m.attempt_number,
        status=FiscalAttemptStatus(m.status),
        request_hash=m.request_hash,
        provider_request_id=m.provider_request_id,
        http_status=m.http_status,
        error_code=m.error_code,
        error_message=m.error_message,
        started_at=m.started_at,
        finished_at=m.finished_at,
        duration_ms=m.duration_ms,
        next_retry_at=m.next_retry_at,
    )
    a.id = m.id
    return a


# =============================================================================
# FISCAL ARTIFACT
# =============================================================================

class SQLAlchemyFiscalArtifactRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_doc_and_type(
        self, doc_id: uuid.UUID, artifact_type: str
    ) -> Optional[FiscalArtifact]:
        q = select(FiscalArtifactModel).where(
            FiscalArtifactModel.fiscal_document_id == doc_id,
            FiscalArtifactModel.artifact_type == artifact_type,
        )
        r = await self.session.execute(q)
        m = r.scalars().first()
        return _to_artifact(m) if m else None

    async def get_by_storage_key(self, storage_key: str):
        """Return the issuance provenance needed to approve a tax-rule XML."""
        result = await self.session.execute(
            select(FiscalArtifactModel, FiscalDocumentModel)
            .join(FiscalDocumentModel, FiscalArtifactModel.fiscal_document_id == FiscalDocumentModel.id)
            .where(FiscalArtifactModel.storage_key == storage_key)
        )
        row = result.first()
        if row is None:
            return None
        artifact, document = row
        from application.services.fiscal.tax_rule_approval_evidence import HomologationFiscalArtifact

        return HomologationFiscalArtifact(
            market_id=document.market_id,
            document_id=document.id,
            document_type=document.document_type,
            environment=document.environment,
            status=document.status,
            access_key=document.access_key,
            protocol=document.protocol,
            authorized_at=document.authorized_at,
            artifact_type=artifact.artifact_type,
            storage_key=artifact.storage_key,
            sha256=artifact.sha256,
        )

    async def save(self, artifact: FiscalArtifact, commit: bool = True) -> FiscalArtifact:
        m = FiscalArtifactModel(id=artifact.id)
        self.session.add(m)
        m.fiscal_document_id = artifact.fiscal_document_id
        m.artifact_type = artifact.artifact_type.value
        m.storage_key = artifact.storage_key
        m.sha256 = artifact.sha256
        m.content_type = artifact.content_type
        m.size_bytes = artifact.size_bytes
        if commit:
            await self.session.commit()
        return artifact


def _to_artifact(m: FiscalArtifactModel) -> FiscalArtifact:
    a = FiscalArtifact(
        fiscal_document_id=m.fiscal_document_id,
        artifact_type=FiscalArtifactType(m.artifact_type),
        storage_key=m.storage_key,
        sha256=m.sha256,
        content_type=m.content_type,
        size_bytes=m.size_bytes,
        created_at=m.created_at,
    )
    a.id = m.id
    return a


# =============================================================================
# FISCAL EVENT
# =============================================================================

class SQLAlchemyFiscalEventRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def append(self, event: FiscalEvent, commit: bool = True) -> FiscalEvent:
        m = FiscalEventModel(id=event.id)
        m.fiscal_document_id = event.fiscal_document_id
        m.market_id = event.market_id
        m.event_type = event.event_type
        m.source = event.source.value
        m.message = event.message
        m.metadata_json_sanitized = json.dumps(event.metadata_json_sanitized) if event.metadata_json_sanitized else None
        self.session.add(m)
        if commit:
            await self.session.commit()
        return event

    async def list_by_document(self, doc_id: uuid.UUID) -> List[FiscalEvent]:
        q = select(FiscalEventModel).where(
            FiscalEventModel.fiscal_document_id == doc_id
        ).order_by(FiscalEventModel.created_at)
        r = await self.session.execute(q)
        return [_to_event(m) for m in r.scalars().all()]


def _to_event(m: FiscalEventModel) -> FiscalEvent:
    e = FiscalEvent(
        fiscal_document_id=m.fiscal_document_id,
        market_id=m.market_id,
        event_type=m.event_type,
        source=FiscalEventSource(m.source),
        message=m.message,
        metadata_json_sanitized=json.loads(m.metadata_json_sanitized) if m.metadata_json_sanitized else None,
        created_at=m.created_at,
    )
    e.id = m.id
    return e


# =============================================================================
# FISCAL USAGE COUNTER
# =============================================================================

class SQLAlchemyFiscalUsageRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_counter(
        self, owner_id: uuid.UUID, period: str
    ) -> Optional[FiscalUsageCounter]:
        """Busca counter owner-level (market_id IS NULL)."""
        q = select(FiscalUsageCounterModel).where(
            FiscalUsageCounterModel.owner_id == owner_id,
            FiscalUsageCounterModel.period_yyyymm == period,
            FiscalUsageCounterModel.market_id == None,
        )
        r = await self.session.execute(q)
        m = r.scalars().first()
        return _to_counter(m) if m else None

    async def get_or_create_counter(
        self,
        owner_id: uuid.UUID,
        period: str,
        included_limit: int = 0,
        commit: bool = True,
    ) -> FiscalUsageCounter:
        counter = await self.get_counter(owner_id, period)
        if counter:
            return counter
        m = FiscalUsageCounterModel(
            id=uuid.uuid4(),
            owner_id=owner_id,
            market_id=None,
            period_yyyymm=period,
            included_limit=included_limit,
        )
        self.session.add(m)
        if commit:
            try:
                await self.session.commit()
                await self.session.refresh(m)
            except Exception:
                await self.session.rollback()
                # Pode ter ocorrido race condition — tenta buscar o registro criado por outro worker
                counter = await self.get_counter(owner_id, period)
                if counter:
                    return counter
                raise
        return _to_counter(m)

    async def upsert_counter(
        self,
        owner_id: uuid.UUID,
        period: str,
        included_limit: int,
        addon_limit: int,
        used_count: int = 0,
        reserved_count: int = 0,
    ) -> None:
        """Cria ou atualiza counter para reset mensal."""
        r = await self.session.execute(
            select(FiscalUsageCounterModel).where(
                FiscalUsageCounterModel.owner_id == owner_id,
                FiscalUsageCounterModel.period_yyyymm == period,
                FiscalUsageCounterModel.market_id == None,
            ).with_for_update()
        )
        m = r.scalars().first()
        if m:
            m.included_limit = included_limit
            m.addon_limit = addon_limit
            m.used_count = used_count
            m.reserved_count = reserved_count
            m.released_count = 0
            m.failed_billable_count = 0
        else:
            m = FiscalUsageCounterModel(
                id=uuid.uuid4(),
                owner_id=owner_id,
                market_id=None,
                period_yyyymm=period,
                included_limit=included_limit,
                addon_limit=addon_limit,
                used_count=used_count,
                reserved_count=reserved_count,
            )
            self.session.add(m)
        await self.session.commit()

    async def increment_reserved(
        self, owner_id: uuid.UUID, period: str, qty: int = 1
    ) -> bool:
        """Incrementa reserved_count atomicamente. Retorna False se limite excedido."""
        r = await self.session.execute(
            select(FiscalUsageCounterModel).where(
                FiscalUsageCounterModel.owner_id == owner_id,
                FiscalUsageCounterModel.period_yyyymm == period,
                FiscalUsageCounterModel.market_id == None,
            ).with_for_update()
        )
        m = r.scalars().first()
        if not m:
            return False
        available_included = max(0, m.included_limit - m.used_count - m.reserved_count)
        available_total = available_included + m.addon_limit
        if available_total < qty:
            return False
        m.reserved_count += qty
        await self.session.commit()
        return True

    async def increment_used(
        self, owner_id: uuid.UUID, period: str, qty: int = 1
    ) -> None:
        await self.session.execute(
            update(FiscalUsageCounterModel)
            .where(
                FiscalUsageCounterModel.owner_id == owner_id,
                FiscalUsageCounterModel.period_yyyymm == period,
                FiscalUsageCounterModel.market_id == None,
            )
            .values(used_count=FiscalUsageCounterModel.used_count + qty)
        )
        await self.session.commit()

    async def decrement_reserved(
        self, owner_id: uuid.UUID, period: str, qty: int = 1
    ) -> None:
        await self.session.execute(
            update(FiscalUsageCounterModel)
            .where(
                FiscalUsageCounterModel.owner_id == owner_id,
                FiscalUsageCounterModel.period_yyyymm == period,
                FiscalUsageCounterModel.market_id == None,
            )
            .values(
                reserved_count=FiscalUsageCounterModel.reserved_count - qty,
                released_count=FiscalUsageCounterModel.released_count + qty,
            )
        )
        await self.session.commit()

    async def increment_addon_limit(
        self, owner_id: uuid.UUID, period: str, amount: int, commit: bool = True
    ) -> None:
        await self.session.execute(
            update(FiscalUsageCounterModel)
            .where(
                FiscalUsageCounterModel.owner_id == owner_id,
                FiscalUsageCounterModel.period_yyyymm == period,
                FiscalUsageCounterModel.market_id == None,
            )
            .values(addon_limit=FiscalUsageCounterModel.addon_limit + amount)
        )
        if commit:
            await self.session.commit()

    async def decrement_addon_limit(
        self, owner_id: uuid.UUID, period: str, amount: int = 1
    ) -> None:
        await self.session.execute(
            update(FiscalUsageCounterModel)
            .where(
                FiscalUsageCounterModel.owner_id == owner_id,
                FiscalUsageCounterModel.period_yyyymm == period,
                FiscalUsageCounterModel.market_id == None,
            )
            .values(addon_limit=FiscalUsageCounterModel.addon_limit - amount)
        )
        await self.session.commit()

    async def get_oldest_active_package(
        self, owner_id: uuid.UUID
    ) -> Optional[FiscalEmissionPackage]:
        """FIFO: pacote pago mais antigo (por valid_until ASC) com remaining > 0."""
        now = datetime.utcnow()
        r = await self.session.execute(
            select(FiscalEmissionPackageModel)
            .where(
                FiscalEmissionPackageModel.owner_id == owner_id,
                FiscalEmissionPackageModel.payment_status == "paid",
                FiscalEmissionPackageModel.remaining > 0,
                (FiscalEmissionPackageModel.valid_until == None)
                | (FiscalEmissionPackageModel.valid_until > now),
            )
            .order_by(FiscalEmissionPackageModel.valid_until.asc().nullsfirst())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        m = r.scalars().first()
        return _to_package(m) if m else None

    async def decrement_package_remaining(self, package_id: uuid.UUID) -> bool:
        """Decrementa remaining atomicamente. Retorna False se remaining já é 0."""
        result = await self.session.execute(
            update(FiscalEmissionPackageModel)
            .where(
                FiscalEmissionPackageModel.id == package_id,
                FiscalEmissionPackageModel.remaining > 0,
            )
            .values(remaining=FiscalEmissionPackageModel.remaining - 1)
        )
        await self.session.commit()
        return result.rowcount > 0

    async def sum_active_packages_qty(self, owner_id: uuid.UUID) -> int:
        """Soma quantity (total original) dos pacotes pagos e válidos — denominador da barra addon."""
        now = datetime.utcnow()
        r = await self.session.execute(
            select(func.coalesce(func.sum(FiscalEmissionPackageModel.quantity), 0)).where(
                FiscalEmissionPackageModel.owner_id == owner_id,
                FiscalEmissionPackageModel.payment_status == "paid",
                (FiscalEmissionPackageModel.valid_until == None)
                | (FiscalEmissionPackageModel.valid_until > now),
            )
        )
        return int(r.scalar() or 0)

    async def sum_active_packages_remaining(self, owner_id: uuid.UUID) -> int:
        """Soma remaining dos pacotes pagos e não expirados (para carryover do reset mensal)."""
        now = datetime.utcnow()
        r = await self.session.execute(
            select(func.coalesce(func.sum(FiscalEmissionPackageModel.remaining), 0)).where(
                FiscalEmissionPackageModel.owner_id == owner_id,
                FiscalEmissionPackageModel.payment_status == "paid",
                FiscalEmissionPackageModel.remaining > 0,
                (FiscalEmissionPackageModel.valid_until == None)
                | (FiscalEmissionPackageModel.valid_until > now),
            )
        )
        return r.scalar() or 0

    async def get_ledger_entry_by_idempotency(
        self, idempotency_key: str
    ) -> Optional[FiscalUsageLedger]:
        q = select(FiscalUsageLedgerModel).where(
            FiscalUsageLedgerModel.idempotency_key == idempotency_key
        )
        r = await self.session.execute(q)
        m = r.scalars().first()
        return _to_ledger(m) if m else None

    async def append_ledger(self, entry: FiscalUsageLedger, commit: bool = True) -> FiscalUsageLedger:
        m = FiscalUsageLedgerModel(id=entry.id)
        m.owner_id = entry.owner_id
        m.market_id = entry.market_id
        m.sale_id = entry.sale_id
        m.fiscal_document_id = entry.fiscal_document_id
        m.period_yyyymm = entry.period_yyyymm
        m.event_type = entry.event_type.value
        m.quantity = entry.quantity
        m.reason = entry.reason
        m.idempotency_key = entry.idempotency_key
        self.session.add(m)
        if commit:
            await self.session.commit()
        return entry

    async def get_owners_with_active_fiscal_config(self) -> List[uuid.UUID]:
        """Retorna owner_ids com fiscal habilitado (para reset mensal)."""
        from infra.database.models import FiscalTenantConfigModel, MarketModel
        r = await self.session.execute(
            select(MarketModel.owner_id).distinct()
            .join(FiscalTenantConfigModel, FiscalTenantConfigModel.market_id == MarketModel.id)
            .where(FiscalTenantConfigModel.enabled == True)
        )
        return list(r.scalars().all())

    async def list_packages(
        self, owner_id: uuid.UUID, active_only: bool = True
    ) -> List[FiscalEmissionPackage]:
        q = select(FiscalEmissionPackageModel).where(
            FiscalEmissionPackageModel.owner_id == owner_id
        )
        if active_only:
            now = datetime.utcnow()
            q = q.where(
                FiscalEmissionPackageModel.payment_status == "paid",
                FiscalEmissionPackageModel.remaining > 0,
                (FiscalEmissionPackageModel.valid_until == None)
                | (FiscalEmissionPackageModel.valid_until > now),
            )
        q = q.order_by(FiscalEmissionPackageModel.valid_until)
        r = await self.session.execute(q)
        return [_to_package(m) for m in r.scalars().all()]

    async def save_package(self, pkg: FiscalEmissionPackage, commit: bool = True) -> FiscalEmissionPackage:
        m = await self.session.get(FiscalEmissionPackageModel, pkg.id)
        if not m:
            m = FiscalEmissionPackageModel(id=pkg.id)
            self.session.add(m)
        m.owner_id = pkg.owner_id
        m.market_id = pkg.market_id
        m.package_type = pkg.package_type
        m.quantity = pkg.quantity
        m.remaining = pkg.remaining
        m.valid_from = pkg.valid_from
        m.valid_until = pkg.valid_until
        m.billing_subscription_id = pkg.billing_subscription_id
        m.payment_status = pkg.payment_status
        m.package_slug = pkg.package_slug
        m.bc_job_id = pkg.bc_job_id
        m.bc_payment_id = pkg.bc_payment_id
        m.bc_idempotency_key = pkg.bc_idempotency_key
        m.price_gross = pkg.price_gross
        m.price_net_target = pkg.price_net_target
        m.purchased_at_market_id = pkg.purchased_at_market_id
        if commit:
            await self.session.commit()
        return pkg

    async def get_package(self, package_id: uuid.UUID) -> Optional[FiscalEmissionPackage]:
        m = await self.session.get(FiscalEmissionPackageModel, package_id)
        return _to_package(m) if m else None

    async def get_package_by_idempotency_key(self, idempotency_key: str) -> Optional[FiscalEmissionPackage]:
        q = select(FiscalEmissionPackageModel).where(
            FiscalEmissionPackageModel.bc_idempotency_key == idempotency_key
        )
        r = await self.session.execute(q)
        m = r.scalars().first()
        return _to_package(m) if m else None

    async def get_package_by_job_id(self, job_id: str) -> Optional[FiscalEmissionPackage]:
        q = select(FiscalEmissionPackageModel).where(
            FiscalEmissionPackageModel.bc_job_id == job_id
        )
        r = await self.session.execute(q)
        m = r.scalars().first()
        return _to_package(m) if m else None


    async def create_package(
        self,
        *,
        owner_id: uuid.UUID,
        market_id: uuid.UUID,
        package_slug: str,
        quantity: int,
        remaining: int,
        price_gross,
        price_net_target,
        payment_status: str,
        bc_idempotency_key: str,
    ) -> FiscalEmissionPackage:
        m = FiscalEmissionPackageModel(
            id=uuid.uuid4(),
            owner_id=owner_id,
            market_id=market_id,
            purchased_at_market_id=market_id,
            package_slug=package_slug,
            quantity=quantity,
            remaining=remaining,
            price_gross=price_gross,
            price_net_target=price_net_target,
            payment_status=payment_status,
            bc_idempotency_key=bc_idempotency_key,
        )
        self.session.add(m)
        try:
            await self.session.commit()
            await self.session.refresh(m)
        except IntegrityError:
            await self.session.rollback()
            existing = await self.get_package_by_idempotency_key(bc_idempotency_key)
            if existing:
                return existing
            raise
        return _to_package(m)

    async def update_package_preference(
        self,
        package_id: uuid.UUID,
        bc_job_id: str,
    ) -> None:
        await self.session.execute(
            update(FiscalEmissionPackageModel)
            .where(FiscalEmissionPackageModel.id == package_id)
            .values(bc_job_id=bc_job_id)
        )
        await self.session.commit()

    async def update_package_job_id(
        self,
        package_id: uuid.UUID,
        job_id: str,
    ) -> None:
        await self.session.execute(
            update(FiscalEmissionPackageModel)
            .where(FiscalEmissionPackageModel.id == package_id)
            .values(bc_job_id=job_id)
        )
        await self.session.commit()

    async def update_package_payment_id(
        self,
        package_id: uuid.UUID,
        bc_payment_id: str,
    ) -> None:
        await self.session.execute(
            update(FiscalEmissionPackageModel)
            .where(FiscalEmissionPackageModel.id == package_id)
            .values(bc_payment_id=bc_payment_id)
        )
        await self.session.commit()


    async def activate_package(
        self,
        package_id: uuid.UUID,
        bc_payment_id: str,
        payment_status: str,
        valid_from: datetime,
        valid_until: datetime,
        commit: bool = True,
    ) -> int:
        result = await self.session.execute(
            update(FiscalEmissionPackageModel)
            .where(
                FiscalEmissionPackageModel.id == package_id,
                FiscalEmissionPackageModel.payment_status == "pending",
            )
            .values(
                bc_payment_id=bc_payment_id,
                payment_status=payment_status,
                valid_from=valid_from,
                valid_until=valid_until,
            )
        )
        if commit:
            await self.session.commit()
        return result.rowcount

    async def mark_package_failed(self, package_id: uuid.UUID, reason: str = "") -> None:
        await self.session.execute(
            update(FiscalEmissionPackageModel)
            .where(FiscalEmissionPackageModel.id == package_id)
            .values(payment_status="failed")
        )
        await self.session.commit()

    async def list_packages_by_owner(
        self,
        owner_id: uuid.UUID,
        limit: int = 20,
        offset: int = 0,
    ) -> List[FiscalEmissionPackage]:
        q = (
            select(FiscalEmissionPackageModel)
            .where(FiscalEmissionPackageModel.owner_id == owner_id)
            .order_by(desc(FiscalEmissionPackageModel.created_at))
            .limit(limit)
            .offset(offset)
        )
        r = await self.session.execute(q)
        return [_to_package(m) for m in r.scalars().all()]

    async def list_packages_pending_since(self, cutoff: datetime) -> List[FiscalEmissionPackage]:
        q = (
            select(FiscalEmissionPackageModel)
            .where(
                FiscalEmissionPackageModel.payment_status == "pending",
                FiscalEmissionPackageModel.created_at <= cutoff,
                FiscalEmissionPackageModel.bc_job_id != None,
            )
            .order_by(FiscalEmissionPackageModel.created_at)
            .limit(100)
        )
        r = await self.session.execute(q)
        return [_to_package(m) for m in r.scalars().all()]

    async def get_pending_packages_with_payment_id_older_than(self, cutoff: datetime, limit: int = 50) -> List[FiscalEmissionPackage]:
        q = (
            select(FiscalEmissionPackageModel)
            .where(
                FiscalEmissionPackageModel.payment_status == "pending",
                FiscalEmissionPackageModel.created_at <= cutoff,
                FiscalEmissionPackageModel.bc_payment_id != None,
            )
            .order_by(FiscalEmissionPackageModel.created_at)
            .limit(limit)
        )
        r = await self.session.execute(q)
        return [_to_package(m) for m in r.scalars().all()]


def _to_counter(m: FiscalUsageCounterModel) -> FiscalUsageCounter:
    c = FiscalUsageCounter(
        owner_id=m.owner_id,
        period_yyyymm=m.period_yyyymm,
        included_limit=m.included_limit,
        addon_limit=m.addon_limit,
        reserved_count=m.reserved_count,
        used_count=m.used_count,
        failed_billable_count=m.failed_billable_count,
        released_count=m.released_count,
        blocked_at=m.blocked_at,
        market_id=m.market_id,
    )
    c.id = m.id
    return c


def _to_package(m: FiscalEmissionPackageModel) -> FiscalEmissionPackage:
    p = FiscalEmissionPackage(
        owner_id=m.owner_id,
        package_type=m.package_type,
        quantity=m.quantity,
        remaining=m.remaining,
        valid_from=m.valid_from,
        valid_until=m.valid_until,
        billing_subscription_id=m.billing_subscription_id,
        payment_status=m.payment_status,
        market_id=getattr(m, "market_id", None),
        package_slug=getattr(m, "package_slug", None),
        bc_job_id=getattr(m, "bc_job_id", None),
        bc_payment_id=getattr(m, "bc_payment_id", None),
        bc_idempotency_key=getattr(m, "bc_idempotency_key", None),
        price_gross=getattr(m, "price_gross", None),
        price_net_target=getattr(m, "price_net_target", None),
        purchased_at_market_id=getattr(m, "purchased_at_market_id", None),
        created_at=m.created_at,
    )
    p.id = m.id
    return p


def _to_ledger(m: FiscalUsageLedgerModel) -> FiscalUsageLedger:
    entry = FiscalUsageLedger(
        owner_id=m.owner_id,
        period_yyyymm=m.period_yyyymm,
        event_type=UsageLedgerEventType(m.event_type),
        market_id=m.market_id,
        sale_id=m.sale_id,
        fiscal_document_id=m.fiscal_document_id,
        quantity=m.quantity,
        reason=m.reason,
        idempotency_key=m.idempotency_key,
        created_at=m.created_at,
    )
    entry.id = m.id
    return entry


# =============================================================================
# PROVIDER WEBHOOK EVENTS
# =============================================================================

class SQLAlchemyProviderWebhookEventRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_event(self, provider: str, event_id: str) -> Optional[ProviderWebhookEvent]:
        q = select(ProviderWebhookEventModel).where(
            ProviderWebhookEventModel.provider == provider,
            ProviderWebhookEventModel.event_id == event_id,
        )
        r = await self.session.execute(q)
        m = r.scalars().first()
        return _to_webhook_event(m) if m else None

    async def create_event(
        self,
        provider: str,
        event_id: str,
        provider_ref: Optional[str],
        raw_payload_hash: str,
        processing_status: str = "received",
    ) -> ProviderWebhookEvent:
        m = ProviderWebhookEventModel(
            id=uuid.uuid4(),
            provider=provider,
            event_id=event_id,
            provider_ref=provider_ref,
            raw_payload_hash=raw_payload_hash,
            processing_status=processing_status,
        )
        self.session.add(m)
        try:
            await self.session.commit()
            await self.session.refresh(m)
        except IntegrityError:
            await self.session.rollback()
            existing = await self.get_event(provider, event_id)
            if existing:
                return existing
            raise
        return _to_webhook_event(m)

    async def mark_processed(self, event_id: uuid.UUID) -> None:
        await self.session.execute(
            update(ProviderWebhookEventModel)
            .where(ProviderWebhookEventModel.id == event_id)
            .values(processing_status="processed", processed_at=datetime.utcnow())
        )
        await self.session.commit()

    async def mark_failed(self, event_id: uuid.UUID) -> None:
        await self.session.execute(
            update(ProviderWebhookEventModel)
            .where(ProviderWebhookEventModel.id == event_id)
            .values(processing_status="failed")
        )
        await self.session.commit()


def _to_webhook_event(m: ProviderWebhookEventModel) -> ProviderWebhookEvent:
    event = ProviderWebhookEvent(
        provider=m.provider,
        event_id=m.event_id,
        provider_ref=m.provider_ref,
        raw_payload_hash=m.raw_payload_hash,
        processing_status=m.processing_status,
        processed_at=m.processed_at,
        created_at=m.created_at,
    )
    event.id = m.id
    return event


# =============================================================================
# FISCAL NOTIFICATION
# =============================================================================

class SQLAlchemyFiscalNotificationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(self, notif: FiscalNotification, commit: bool = True) -> FiscalNotification:
        # Verifica dedupe
        if notif.dedupe_key:
            q = select(FiscalNotificationModel).where(
                FiscalNotificationModel.dedupe_key == notif.dedupe_key
            )
            r = await self.session.execute(q)
            if r.scalars().first():
                return notif  # já existe — idempotente

        m = FiscalNotificationModel(id=notif.id)
        m.owner_id = notif.owner_id
        m.market_id = notif.market_id
        m.notification_type = notif.notification_type.value
        m.severity = notif.severity.value
        m.title = notif.title
        m.message = notif.message
        m.dedupe_key = notif.dedupe_key
        self.session.add(m)
        if commit:
            await self.session.commit()
        return notif

    async def list_unread(
        self, owner_id: uuid.UUID, limit: int = 50
    ) -> List[FiscalNotification]:
        q = select(FiscalNotificationModel).where(
            FiscalNotificationModel.owner_id == owner_id,
            FiscalNotificationModel.read_at == None,
        ).order_by(desc(FiscalNotificationModel.created_at)).limit(limit)
        r = await self.session.execute(q)
        return [_to_notification(m) for m in r.scalars().all()]

    async def mark_read(self, notif_id: uuid.UUID, owner_id: uuid.UUID):
        await self.session.execute(
            update(FiscalNotificationModel)
            .where(
                FiscalNotificationModel.id == notif_id,
                FiscalNotificationModel.owner_id == owner_id,
            )
            .values(read_at=datetime.utcnow())
        )
        await self.session.commit()


def _to_notification(m: FiscalNotificationModel) -> FiscalNotification:
    n = FiscalNotification(
        owner_id=m.owner_id,
        market_id=m.market_id,
        notification_type=NotificationType(m.notification_type),
        severity=NotificationSeverity(m.severity),
        title=m.title,
        message=m.message,
        dedupe_key=m.dedupe_key,
        read_at=m.read_at,
        sent_email_at=m.sent_email_at,
        created_at=m.created_at,
    )
    n.id = m.id
    return n


# =============================================================================
# INUTILIZAÇÃO
# =============================================================================

class SQLAlchemyFiscalInutilizacaoRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_idempotency_key(self, key: str) -> Optional[FiscalNumberInutilizacao]:
        q = select(FiscalInutilizacaoModel).where(FiscalInutilizacaoModel.idempotency_key == key)
        r = await self.session.execute(q)
        m = r.scalars().first()
        return _to_inutilizacao(m) if m else None

    async def save(self, entity: FiscalNumberInutilizacao) -> FiscalNumberInutilizacao:
        if not hasattr(entity, "id") or entity.id is None:
            entity.id = uuid.uuid4()

        existing = await self.session.get(FiscalInutilizacaoModel, entity.id)
        if existing:
            existing.status = entity.status
            existing.protocol = entity.protocol
            existing.provider_message = entity.provider_message
            existing.error_message = entity.error_message
            existing.authorized_at = entity.authorized_at
            m = existing
        else:
            m = FiscalInutilizacaoModel(
                id=entity.id,
                market_id=entity.market_id,
                provider=entity.provider,
                environment=entity.environment.value,
                cnpj=entity.cnpj,
                document_type=entity.document_type,
                series=entity.series,
                start_number=entity.start_number,
                end_number=entity.end_number,
                justification=entity.justification,
                requested_by_id=entity.requested_by_id,
                status=entity.status,
                protocol=entity.protocol,
                provider_message=entity.provider_message,
                error_message=entity.error_message,
                authorized_at=entity.authorized_at,
                idempotency_key=entity.idempotency_key,
            )
            self.session.add(m)

        await self.session.commit()
        await self.session.refresh(m)
        return _to_inutilizacao(m)

    async def list_by_market(
        self,
        market_id: uuid.UUID,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[FiscalNumberInutilizacao], int]:
        filters = [FiscalInutilizacaoModel.market_id == market_id]
        if status:
            filters.append(FiscalInutilizacaoModel.status == status)

        count_q = select(func.count()).select_from(
            select(FiscalInutilizacaoModel).where(*filters).subquery()
        )
        total = (await self.session.execute(count_q)).scalar() or 0

        q = (
            select(FiscalInutilizacaoModel)
            .where(*filters)
            .order_by(desc(FiscalInutilizacaoModel.created_at))
            .limit(limit)
            .offset(offset)
        )
        r = await self.session.execute(q)
        return [_to_inutilizacao(m) for m in r.scalars().all()], total


def _to_inutilizacao(m: FiscalInutilizacaoModel) -> FiscalNumberInutilizacao:
    entity = FiscalNumberInutilizacao(
        market_id=m.market_id,
        provider=m.provider,
        environment=FiscalEnvironment(m.environment),
        cnpj=m.cnpj,
        document_type=m.document_type,
        series=m.series,
        start_number=m.start_number,
        end_number=m.end_number,
        justification=m.justification,
        requested_by_id=m.requested_by_id,
        status=m.status,
        protocol=m.protocol,
        provider_message=m.provider_message,
        error_message=m.error_message,
        authorized_at=m.authorized_at,
        idempotency_key=m.idempotency_key,
    )
    entity.id = m.id
    entity.created_at = m.created_at
    return entity
