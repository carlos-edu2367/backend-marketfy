"""Production integrity rules for versioned product fiscal classifications."""
from __future__ import annotations

import hashlib
import asyncio
import os
import uuid
from datetime import date, datetime
from pathlib import Path
import sys

import pytest
import pytest_asyncio

APP_DIR = Path(__file__).resolve().parents[2] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.append(str(APP_DIR))

from domain.fiscal import ProductTaxRule, ProductTaxRuleStatus, TaxRuleApproval
from domain.identity import UserRole
from infra.security.fiscal_rule_authorization import (
    TaxRuleApprovalEvidenceError,
    assert_tax_rule_approver,
)


def _published_rule(**overrides) -> ProductTaxRule:
    values = {
        "market_id": uuid.uuid4(),
        "name": "Refrigerante ST",
        "status": ProductTaxRuleStatus.PUBLISHED,
        "effective_from": date(2026, 7, 14),
        "ncm": "22021000",
        "cest": "0300700",
        "origin": "0",
        "cfop": "5405",
        "icms_group": "ICMSSN500",
        "icms_csosn": "500",
        "icms_st_mod_bc": "4",
        "icms_st_mva_rate": 40,
        "icms_st_rate": 18,
        "pis_cst": "07",
        "pis_rate": 0,
        "cofins_cst": "07",
        "cofins_rate": 0,
    }
    values.update(overrides)
    return ProductTaxRule(**values)


@pytest.mark.asyncio
async def test_owner_cannot_publish_without_accountant_evidence():
    with pytest.raises(TaxRuleApprovalEvidenceError) as exc_info:
        assert_tax_rule_approver(UserRole.OWNER)

    assert exc_info.value.code == "tax_rule.approval_evidence_missing"


@pytest.mark.asyncio
async def test_accountant_approval_uses_authenticated_actor_and_server_time():
    rule_id = uuid.uuid4()
    accountant_id = uuid.uuid4()
    before = datetime.utcnow()

    approval = TaxRuleApproval.from_authenticated_actor(
        rule_id=rule_id,
        accountant_user_id=accountant_id,
        homologation_xml_reference="s3://homologation/nfce-st-001.xml",
    )

    assert approval.rule_id == rule_id
    assert approval.accountant_user_id == accountant_id
    assert approval.approved_at >= before
    assert approval.homologation_xml_sha256 == hashlib.sha256(
        b"s3://homologation/nfce-st-001.xml"
    ).hexdigest()


@pytest.mark.asyncio
async def test_successor_is_next_version_in_same_family():
    previous = _published_rule()

    successor = previous.create_successor(effective_from=date(2026, 8, 1))

    assert successor.status is ProductTaxRuleStatus.DRAFT
    assert successor.rule_family_id == previous.rule_family_id
    assert successor.version == previous.version + 1
    assert successor.supersedes_rule_id == previous.id


@pytest_asyncio.fixture
async def pg_pool():
    """Optional PostgreSQL DSN for verifying the real exclusion constraint."""
    dsn = os.getenv("TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("TEST_POSTGRES_DSN não configurado para teste de concorrência PostgreSQL")
    import asyncpg

    pool = await asyncpg.create_pool(dsn)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_concurrent_assignments_cannot_overlap(pg_pool):
    """The migration must make one of two overlapping writes fail in PostgreSQL."""
    import asyncpg

    market_id, owner_id, product_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    first_rule_id, second_rule_id = uuid.uuid4(), uuid.uuid4()
    family_one, family_two = uuid.uuid4(), uuid.uuid4()
    async with pg_pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO users (id, name, email, password_hash, role, is_active) VALUES ($1, $2, $3, $4, $5, true)",
            owner_id, "Owner", f"{owner_id}@example.test", "hash", "owner",
        )
        await connection.execute(
            "INSERT INTO markets (id, owner_id, name, document, address, is_active) VALUES ($1, $2, $3, $4, $5, true)",
            market_id, owner_id, "Market", str(uuid.uuid4()).replace("-", "")[:14], "Rua 1",
        )
        await connection.execute(
            "INSERT INTO products (id, market_id, name, code, price, active) VALUES ($1, $2, $3, $4, $5, true)",
            product_id, market_id, "Produto", "SKU-1", 10,
        )
        for rule_id, family_id, name in (
            (first_rule_id, family_one, "Regra 1"),
            (second_rule_id, family_two, "Regra 2"),
        ):
            await connection.execute(
                """INSERT INTO product_tax_rules
                   (id, market_id, name, status, version, rule_family_id)
                   VALUES ($1, $2, $3, 'published', 1, $4)""",
                rule_id, market_id, name, family_id,
            )

    async def assign(rule_id):
        try:
            async with pg_pool.acquire() as connection:
                await connection.execute(
                    """INSERT INTO product_tax_rule_assignments
                       (id, market_id, product_id, tax_rule_id, effective_from, effective_to)
                       VALUES ($1, $2, $3, $4, DATE '2026-07-14', NULL)""",
                    uuid.uuid4(), market_id, product_id, rule_id,
                )
            return "assigned"
        except (asyncpg.ExclusionViolationError, asyncpg.DeadlockDetectedError):
            return "tax_rule.assignment_conflict"

    results = await asyncio.gather(assign(first_rule_id), assign(second_rule_id))
    assert sorted(results) == ["assigned", "tax_rule.assignment_conflict"]
