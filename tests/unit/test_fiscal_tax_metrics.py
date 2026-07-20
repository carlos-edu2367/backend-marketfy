import asyncio
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace



APP_DIR = Path(__file__).resolve().parents[2] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.append(str(APP_DIR))


def test_global_switch_makes_effective_enforcement_off_without_changing_market_mode():
    from application.services.fiscal.fiscal_rollout_service import (
        effective_fiscal_rule_enforcement,
    )
    from domain.fiscal import FiscalRuleEnforcement

    assert (
        effective_fiscal_rule_enforcement(
            FiscalRuleEnforcement.BLOCK, product_rules_enabled=False
        )
        is FiscalRuleEnforcement.OFF
    )
    assert (
        effective_fiscal_rule_enforcement(
            FiscalRuleEnforcement.BLOCK, product_rules_enabled=True
        )
        is FiscalRuleEnforcement.BLOCK
    )


def test_tax_contract_metrics_have_only_bounded_non_tenant_labels():
    from infra.observability.metrics import MetricsRegistry

    registry = MetricsRegistry()
    market_id = str(uuid.uuid4())
    registry.record_fiscal_contract(
        market_id=market_id,
        contract_version="marketfy.fiscal-tax-snapshot.v2",
        enforcement_mode="block",
        result_code="sale.fiscal_rule_missing",
        path="v2",
    )

    output = registry.to_prometheus_text()

    assert "marketfy_fiscal_contract_total" in output
    assert 'contract_version="marketfy.fiscal-tax-snapshot.v2"' in output
    assert 'enforcement_mode="block"' in output
    assert 'result_code="sale.fiscal_rule_missing"' in output
    assert 'path="v2"' in output
    assert market_id not in output
    assert "market=" not in output


def test_tax_contract_metrics_bucket_unrecognized_rollout_labels_as_other():
    from infra.observability.metrics import MetricsRegistry

    registry = MetricsRegistry()
    registry.record_fiscal_contract(
        market_id=str(uuid.uuid4()),
        contract_version="caller-controlled-contract",
        enforcement_mode="caller-controlled-mode",
        result_code="caller-controlled-result",
        path="caller-controlled-path",
    )
    registry.record_fiscal_rule_event(
        market_id=str(uuid.uuid4()),
        mode="caller-controlled-mode",
        event="contract_rejected",
    )

    output = registry.to_prometheus_text()

    assert 'contract_version="other"' in output
    assert 'enforcement_mode="other"' in output
    assert 'result_code="other"' in output
    assert 'path="other"' in output
    assert 'mode="other"' in output
    assert "caller-controlled" not in output


def test_preflight_records_bounded_rule_and_contract_metrics() -> None:
    from application.services.fiscal.tax_rule_service import TaxRuleNotFoundError
    from application.services.sales_service import SalesService
    from domain.fiscal import FiscalRuleEnforcement

    market_id = uuid.uuid4()
    product_id = uuid.uuid4()
    metric_calls: list[tuple[str, dict]] = []

    class ProductRepository:
        async def get_by_id(self, requested_id):
            if requested_id == product_id:
                return SimpleNamespace(id=product_id, market_id=market_id, name="Produto")
            return None

    class ConfigRepository:
        async def get_by_market(self, _market_id):
            return SimpleNamespace(fiscal_rule_enforcement=FiscalRuleEnforcement.WARN)

    class RuleService:
        async def resolve_for_sale_item(self, **_kwargs):
            raise TaxRuleNotFoundError("missing")

    class Metrics:
        def record_fiscal_rule_event(self, **kwargs):
            metric_calls.append(("rule", kwargs))

        def record_fiscal_contract(self, **kwargs):
            metric_calls.append(("contract", kwargs))

    service = SalesService(
        sale_repo=SimpleNamespace(), box_repo=SimpleNamespace(), product_repo=ProductRepository(),
        market_repo=SimpleNamespace(), terminal_repo=SimpleNamespace(), user_repo=SimpleNamespace(),
        plan_repo=SimpleNamespace(), financial_repo=SimpleNamespace(),
        fiscal_config_repo=ConfigRepository(), tax_rule_service=RuleService(), metrics=Metrics(),
    )

    asyncio.run(
        service.fiscal_preflight(
            market_id=market_id,
            occurred_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
            items=[SimpleNamespace(product_id=product_id, quantity=Decimal("1"))],
        )
    )

    assert ("rule", {"market_id": str(market_id), "mode": "warn", "event": "fiscal_rule_missing"}) in metric_calls
    assert (
        "contract",
        {
            "market_id": str(market_id),
            "contract_version": "marketfy.fiscal-tax-snapshot.v2",
            "enforcement_mode": "warn",
            "result_code": "sale.fiscal_rule_missing",
            "path": "v2",
        },
    ) in metric_calls


def test_persisted_contract_failure_records_contract_rejected_rule_metric(monkeypatch) -> None:
    from application.jobs import fiscal_jobs
    from infra.observability.metrics import MetricsRegistry

    registry = MetricsRegistry()
    monkeypatch.setattr("infra.observability.metrics.metrics_registry", registry)
    market_id = str(uuid.uuid4())

    fiscal_jobs._record_fiscal_contract_metric(
        SimpleNamespace(market_id=market_id),
        "block",
        "marketfy.fiscal-tax-snapshot.v2",
        "payload_invalid",
        "v2",
    )

    output = registry.to_prometheus_text()

    assert 'result_code="payload_invalid"' in output
    assert 'event="contract_rejected"' in output
    assert market_id not in output
