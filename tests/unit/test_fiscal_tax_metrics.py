import sys
import uuid
from pathlib import Path


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
