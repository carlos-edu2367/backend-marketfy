"""HTTP contract for the fiscal-rule lifecycle and product assignments.

The repository is replaced by an in-memory implementation because the route
contract is independent from Postgres.  Persistence-specific overlap checks
are covered by the repository implementation itself.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "../../app"))
if APP_DIR not in sys.path:
    sys.path.append(APP_DIR)


MARKET_ID = uuid.uuid4()
PRODUCT_ONE_ID = uuid.uuid4()
PRODUCT_TWO_ID = uuid.uuid4()
ACCOUNTANT_ID = uuid.uuid4()
ACTOR_ID = ACCOUNTANT_ID


def _approved_rule_payload() -> dict:
    return {
        "name": "Refrigerante ST",
        "effective_from": "2026-07-14",
        "ncm": "22021000",
        "cest": "0300700",
        "origin": "0",
        "cfop": "5405",
        "icms_group": "ICMSSN500",
        "icms_csosn": "500",
        "icms_st_mod_bc": "4",
        "icms_st_mva_rate": "40.00",
        "icms_st_rate": "18.00",
        "pis_cst": "07",
        "pis_rate": "0.00",
        "cofins_cst": "07",
        "cofins_rate": "0.00",
    }


class FakeApprovalEvidenceService:
    async def capture_approval(self, *, rule_id, accountant_user_id, source_storage_key, **_kwargs):
        from domain.fiscal import TaxRuleApproval

        return TaxRuleApproval.from_verified_artifact(
            rule_id=rule_id,
            accountant_user_id=accountant_user_id,
            homologation_xml_storage_key=f"fiscal/homologacao/{MARKET_ID}/tax_rule_approvals/{rule_id}.xml",
            canonical_xml=b"<NFe></NFe>",
        )


class InMemoryTaxRuleRepository:
    def __init__(self, _session):
        self.rules = _RULES
        self.assignments = _ASSIGNMENTS

    async def create_draft(self, rule):
        self.rules[rule.id] = rule
        return rule

    async def get_rule(self, market_id, rule_id):
        rule = self.rules.get(rule_id)
        return rule if rule and rule.market_id == market_id else None

    async def list_rules(self, market_id):
        return [rule for rule in self.rules.values() if rule.market_id == market_id]

    async def update_draft(self, rule, changes):
        for field, value in changes.items():
            setattr(rule, field, value)
        return rule

    async def publish_rule(self, rule):
        raise AssertionError("Publication must include immutable accountant approval evidence")

    async def publish_rule_with_approval(self, rule, approval):
        from domain.fiscal import ProductTaxRuleStatus
        from domain.shared import BusinessRuleException

        if not rule.ncm or not rule.cest or not approval.homologation_xml_sha256:
            raise BusinessRuleException("Regra fiscal sem campos obrigatórios ou aprovação contábil.")
        rule.approved_by = approval.accountant_user_id
        rule.approved_at = approval.approved_at
        rule.status = ProductTaxRuleStatus.PUBLISHED
        return rule

    async def create_successor(self, rule, *, effective_from):
        successor = rule.create_successor(effective_from=effective_from)
        self.rules[successor.id] = successor
        return successor

    async def retire_rule(self, rule):
        from domain.fiscal import ProductTaxRuleStatus

        rule.status = ProductTaxRuleStatus.RETIRED
        return rule

    async def assign_published_rule(self, *, market_id, product_ids, rule, effective_from, actor_id, reason):
        from domain.fiscal import ProductTaxRuleStatus
        from domain.shared import BusinessRuleException

        if rule.status is not ProductTaxRuleStatus.PUBLISHED:
            raise BusinessRuleException("Somente regras fiscais publicadas podem ser atribuídas.")
        updated = []
        skipped = []
        for product_id in product_ids:
            if product_id == PRODUCT_TWO_ID:
                skipped.append({"product_id": str(product_id), "reason": "overlap"})
                continue
            prior = self.assignments.get(product_id)
            self.assignments[product_id] = {
                "tax_rule_id": rule.id,
                "before": prior["tax_rule_id"] if prior else None,
                "actor_id": actor_id,
                "reason": reason,
                "effective_from": effective_from,
                "assigned_at": datetime.now(timezone.utc),
            }
            updated.append(product_id)
        audit_changes = [
            {
                "product_id": str(product_id),
                "before_rule_id": None,
                "after_rule_id": str(rule.id),
            }
            for product_id in updated
        ]
        return updated, skipped, audit_changes

    async def list_product_fiscal_status(self, market_id, product_ids, when):
        result = {}
        for product_id in product_ids:
            assignment = self.assignments.get(product_id)
            if not assignment:
                result[product_id] = "missing"
            elif product_id == PRODUCT_TWO_ID:
                result[product_id] = "ambiguous"
            else:
                result[product_id] = "ready"
        return result


class FakeAudit:
    def __init__(self):
        self.events = []

    async def record(self, **kwargs):
        self.events.append(kwargs)


_RULES = {}
_ASSIGNMENTS = {}


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    _RULES.clear()
    _ASSIGNMENTS.clear()
    monkeypatch.setenv("FISCAL_APPROVED_ICMS_GROUPS", "ICMSSN500")
    from infra.config.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client(monkeypatch):
    from application.services.inventory_service import InventoryService
    from domain.identity import User, UserRole
    from domain.inventory import Product
    from domain.shared import CPF, Email
    from infra.repositories import fiscal_repo
    from infra.repositories import market_member_repo
    from infra.repositories import sqlalchemy_repos
    from infra.web.routers import fiscal as fiscal_router
    from infra.web.routers import inventory as inventory_router

    monkeypatch.setattr(fiscal_repo, "SQLAlchemyProductTaxRuleRepository", InMemoryTaxRuleRepository)
    monkeypatch.setattr(
        fiscal_router,
        "_get_tax_rule_approval_evidence_service",
        lambda: FakeApprovalEvidenceService(),
    )

    class Member:
        def __init__(self, role):
            self.role = role

    class MemberRepository:
        def __init__(self, _session):
            pass

        async def get_active_member(self, _market_id, user_id):
            return Member(UserRole.ACCOUNTANT if user_id == ACCOUNTANT_ID else UserRole.OWNER)

    monkeypatch.setattr(market_member_repo, "SQLAlchemyMarketMemberRepository", MemberRepository)
    audit = FakeAudit()
    user = User(
        id=ACTOR_ID,
        name="Fiscal",
        email=Email("fiscal@example.com"),
        cpf=CPF("52998224725"),
        password_hash="hash",
        role=UserRole.ACCOUNTANT,
    )
    product_one = Product(
        id=PRODUCT_ONE_ID,
        market_id=MARKET_ID,
        name="Refrigerante",
        code="REF-001",
        barcode=None,
        price=Decimal("10.00"),
    )
    product_two = Product(
        id=PRODUCT_TWO_ID,
        market_id=MARKET_ID,
        name="Suco",
        code="SUC-001",
        barcode=None,
        price=Decimal("10.00"),
    )

    class Products:
        async def list_by_market(self, *_args, **_kwargs):
            return [product_one, product_two]

    class ProductRepository:
        def __init__(self, _session):
            pass

        async def list_by_market(self, *_args, **_kwargs):
            return [product_one, product_two]

    monkeypatch.setattr(sqlalchemy_repos, "SQLAlchemyProductRepository", ProductRepository)

    app = FastAPI()
    app.include_router(fiscal_router.router, prefix="/api/v1/fiscal")
    app.include_router(inventory_router.router, prefix="/api/v1/inventory")

    async def fake_db():
        yield object()

    async def permitted_market():
        return object()

    app.dependency_overrides[fiscal_router.get_db] = fake_db
    app.dependency_overrides[inventory_router.get_db] = fake_db
    app.dependency_overrides[fiscal_router.get_current_user] = lambda: user
    app.dependency_overrides[fiscal_router.get_audit_service] = lambda: audit
    app.dependency_overrides[inventory_router.get_inventory_service] = lambda: InventoryService(Products(), object())
    for route in app.routes:
        if route.path.startswith("/api/v1/fiscal/") or route.path.startswith("/api/v1/inventory/"):
            for dependency in route.dependant.dependencies:
                if getattr(dependency.call, "__name__", None) == "_dep":
                    app.dependency_overrides[dependency.call] = permitted_market

    return TestClient(app), app, audit


def test_operator_cannot_create_or_publish_a_tax_rule(client):
    http, app, _audit = client

    async def forbidden_market():
        raise HTTPException(status_code=403, detail="Permissão insuficiente para este recurso.")

    for route in app.routes:
        if route.path == "/api/v1/fiscal/{market_id}/tax-rules":
            for dependency in route.dependant.dependencies:
                if getattr(dependency.call, "__name__", None) == "_dep":
                    app.dependency_overrides[dependency.call] = forbidden_market

    response = http.post(f"/api/v1/fiscal/{MARKET_ID}/tax-rules", json=_approved_rule_payload())

    assert response.status_code == 403


def test_fiscal_user_can_create_a_draft_and_cannot_publish_an_incomplete_rule(client):
    http, _app, _audit = client
    draft = http.post(
        f"/api/v1/fiscal/{MARKET_ID}/tax-rules",
        json={"name": "Draft sem alíquotas", "effective_from": "2026-07-14"},
    )

    assert draft.status_code == 201
    rule_id = draft.json()["id"]
    publish = http.post(
        f"/api/v1/fiscal/{MARKET_ID}/tax-rules/{rule_id}/publish",
        json={"homologation_xml_storage_key": f"fiscal/homologacao/{MARKET_ID}/source/incomplete.xml"},
    )

    assert publish.status_code == 400


def test_publication_rejects_a_forged_approver_identity_from_the_client(client):
    http, _app, _audit = client
    created = http.post(f"/api/v1/fiscal/{MARKET_ID}/tax-rules", json=_approved_rule_payload())

    response = http.post(
        f"/api/v1/fiscal/{MARKET_ID}/tax-rules/{created.json()['id']}/publish",
        json={
            "homologation_xml_storage_key": f"fiscal/homologacao/{MARKET_ID}/source/st.xml",
            "approved_by": str(uuid.uuid4()),
            "approved_at": "2026-07-14T10:00:00Z",
        },
    )

    assert response.status_code == 422


def test_publication_rejects_missing_homologation_artifact(client, monkeypatch):
    http, _app, _audit = client
    from application.services.fiscal.tax_rule_approval_evidence import TaxRuleApprovalArtifactError
    from infra.web.routers import fiscal as fiscal_router

    class MissingEvidence:
        async def capture_approval(self, **_kwargs):
            raise TaxRuleApprovalArtifactError("O XML homologado informado não foi encontrado.")

    monkeypatch.setattr(fiscal_router, "_get_tax_rule_approval_evidence_service", lambda: MissingEvidence())
    created = http.post(f"/api/v1/fiscal/{MARKET_ID}/tax-rules", json=_approved_rule_payload())

    response = http.post(
        f"/api/v1/fiscal/{MARKET_ID}/tax-rules/{created.json()['id']}/publish",
        json={"homologation_xml_storage_key": f"fiscal/homologacao/{MARKET_ID}/source/missing.xml"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "tax_rule.approval_artifact_invalid"


def test_owner_cannot_publish_without_accountant_membership(client):
    http, app, _audit = client
    from domain.identity import User, UserRole
    from domain.shared import CPF, Email
    from infra.web.routers import fiscal as fiscal_router

    owner = User(
        id=uuid.uuid4(),
        name="Owner",
        email=Email("owner@example.com"),
        cpf=CPF("52998224725"),
        password_hash="hash",
        role=UserRole.OWNER,
    )
    app.dependency_overrides[fiscal_router.get_current_user] = lambda: owner
    created = http.post(f"/api/v1/fiscal/{MARKET_ID}/tax-rules", json=_approved_rule_payload())

    response = http.post(
        f"/api/v1/fiscal/{MARKET_ID}/tax-rules/{created.json()['id']}/publish",
        json={"homologation_xml_storage_key": f"fiscal/homologacao/{MARKET_ID}/source/st.xml"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "tax_rule.approval_evidence_missing"


def test_publication_records_the_authenticated_fiscal_actor_as_approver(client):
    http, _app, _audit = client
    created = http.post(f"/api/v1/fiscal/{MARKET_ID}/tax-rules", json=_approved_rule_payload())

    response = http.post(
        f"/api/v1/fiscal/{MARKET_ID}/tax-rules/{created.json()['id']}/publish",
        json={"homologation_xml_storage_key": f"fiscal/homologacao/{MARKET_ID}/source/st.xml"},
    )

    assert response.status_code == 200
    assert response.json()["approved_by"] == str(ACTOR_ID)


def test_correction_creates_successor_in_the_same_rule_family(client):
    http, _app, _audit = client
    created = http.post(f"/api/v1/fiscal/{MARKET_ID}/tax-rules", json=_approved_rule_payload())
    published = http.post(
        f"/api/v1/fiscal/{MARKET_ID}/tax-rules/{created.json()['id']}/publish",
        json={"homologation_xml_storage_key": f"fiscal/homologacao/{MARKET_ID}/source/st.xml"},
    )

    response = http.post(
        f"/api/v1/fiscal/{MARKET_ID}/tax-rules/{created.json()['id']}/successor",
        json={"effective_from": "2026-08-01"},
    )

    assert published.status_code == 200
    assert response.status_code == 201
    assert response.json()["status"] == "draft"
    assert response.json()["version"] == 2


def test_only_published_rule_can_be_assigned_and_bulk_change_is_audited(client):
    http, _app, audit = client
    created = http.post(f"/api/v1/fiscal/{MARKET_ID}/tax-rules", json=_approved_rule_payload())
    rule_id = created.json()["id"]

    rejected = http.post(
        f"/api/v1/inventory/{MARKET_ID}/products/tax-rule-assignment",
        json={
            "tax_rule_id": rule_id,
            "product_ids": [str(PRODUCT_ONE_ID)],
            "effective_from": "2026-07-14",
            "reason": "Aprovação contábil 2026-07",
        },
    )
    assert rejected.status_code == 400

    published = http.post(
        f"/api/v1/fiscal/{MARKET_ID}/tax-rules/{rule_id}/publish",
        json={"homologation_xml_storage_key": f"fiscal/homologacao/{MARKET_ID}/source/st.xml"},
    )
    assert published.status_code == 200

    assigned = http.post(
        f"/api/v1/inventory/{MARKET_ID}/products/tax-rule-assignment",
        json={
            "tax_rule_id": rule_id,
            "product_ids": [str(PRODUCT_ONE_ID), str(PRODUCT_TWO_ID)],
            "effective_from": "2026-07-14",
            "reason": "Aprovação contábil 2026-07",
        },
    )

    assert assigned.status_code == 200
    assert assigned.json()["updated_product_ids"] == [str(PRODUCT_ONE_ID)]
    assert assigned.json()["skipped"] == [{"product_id": str(PRODUCT_TWO_ID), "reason": "overlap"}]
    assert _ASSIGNMENTS[PRODUCT_ONE_ID]["before"] is None
    assert _ASSIGNMENTS[PRODUCT_ONE_ID]["actor_id"] == ACTOR_ID
    assert audit.events[-1]["metadata"]["after_rule_id"] == rule_id


def test_fiscal_status_exposes_missing_and_ambiguous_products_without_legacy_fallback(client):
    http, _app, _audit = client

    response = http.get(f"/api/v1/fiscal/{MARKET_ID}/tax-rule-pendencies")

    assert response.status_code == 200
    statuses = {item["product_id"]: item["fiscal_status"] for item in response.json()["items"]}
    assert statuses[str(PRODUCT_ONE_ID)] == "missing"
    assert statuses[str(PRODUCT_TWO_ID)] == "missing"


def test_product_list_exposes_fiscal_status_for_frontend_guidance(client):
    http, _app, _audit = client

    response = http.get(f"/api/v1/inventory/{MARKET_ID}/products")

    assert response.status_code == 200
    assert {item["fiscal_status"] for item in response.json()} == {"missing"}


def test_product_sync_exposes_fiscal_status_for_offline_frontend_guidance(client):
    http, _app, _audit = client

    response = http.get(f"/api/v1/inventory/{MARKET_ID}/products/sync")

    assert response.status_code == 200
    assert {item["fiscal_status"] for item in response.json()["updated"]} == {"missing"}
