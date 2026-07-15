"""The fiscal-rule rollout must default every tenant to legacy-safe off mode."""
from __future__ import annotations

import sys
import uuid
from importlib import util
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[2] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.append(str(APP_DIR))


def test_tenant_config_defaults_to_explicit_legacy_safe_enforcement_off():
    import domain.fiscal as fiscal

    assert hasattr(fiscal, "FiscalRuleEnforcement")
    config = fiscal.FiscalTenantConfig(market_id=uuid.uuid4())

    assert config.fiscal_rule_enforcement is fiscal.FiscalRuleEnforcement.OFF


def test_enforcement_migration_exists_after_current_tax_rule_approval_revision():
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260715_0005_fiscal_rule_enforcement.py"
    )

    assert migration_path.exists()
    spec = util.spec_from_file_location("fiscal_rule_enforcement_migration", migration_path)
    module = util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    assert module.down_revision == "20260715_0004"
