import json
import sys
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest


APP_DIR = Path(__file__).resolve().parents[2] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.append(str(APP_DIR))


@dataclass
class Product:
    id: uuid.UUID
    code: str
    name: str
    ncm: str | None
    tax_rule_id: uuid.UUID | None = None
    active: bool = True
    deleted_at: object | None = None


class ProductRepository:
    def __init__(self, products):
        self.products = products

    async def list_by_market(self, _market_id):
        return self.products

    async def snapshot_rows(self, _market_id):
        return [product.__dict__.copy() for product in self.products]


class RuleRepository:
    async def list_product_rule_associations(self, _market_id, product_ids):
        return {product_id: [] for product_id in product_ids}


class ConfigRepository:
    async def get_by_market(self, _market_id):
        return type(
            "Config",
            (),
            {
                "tax_regime": "simples_nacional",
                "address_json": {"uf": "GO"},
                "document_model": "65",
            },
        )()


class RuleService:
    async def list_pendencies(self, **_kwargs):
        from application.services.fiscal.tax_rule_service import (
            TaxRulePendency,
            TaxRulePendencyReport,
        )

        products = _kwargs["products"]
        return TaxRulePendencyReport(
            items=[
                TaxRulePendency(product.id, product.name, "missing")
                for product in products
            ],
            summary={"missing": len(products), "total": len(products)},
        )


@pytest.mark.asyncio
async def test_report_is_read_only_and_outputs_safe_csv_json():
    from application.jobs.fiscal_tax_migration_job import FiscalTaxMigrationJob

    market_id = uuid.uuid4()
    products = [
        Product(uuid.uuid4(), "BEB-001", "Bebida", "22030000"),
        Product(uuid.uuid4(), "ALM-002", "Alimento", None),
    ]
    product_repo = ProductRepository(products)
    job = FiscalTaxMigrationJob(
        product_repository=product_repo,
        tax_rule_repository=RuleRepository(),
        tax_rule_service=RuleService(),
        config_repository=ConfigRepository(),
        today=lambda: date(2026, 7, 20),
    )

    before = await product_repo.snapshot_rows(market_id)
    report = await job.run(market_id)
    after = await product_repo.snapshot_rows(market_id)

    assert before == after
    assert report.counts["missing"] == 2
    payload = json.loads(report.to_json())
    assert payload["counts"]["missing"] == 2
    assert payload["products"][0] == {
        "product_id": str(products[0].id),
        "code": "BEB-001",
        "name": "Bebida",
        "ncm": "22030000",
        "current_rule_id": None,
        "rule_status": None,
        "effective_from": None,
        "effective_to": None,
        "pendency_code": "missing",
    }
    assert "price" not in report.to_csv().lower()
    assert "credential" not in report.to_json().lower()
