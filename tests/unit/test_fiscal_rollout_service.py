import sys
import uuid
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[2] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.append(str(APP_DIR))

from domain.fiscal import FiscalRuleEnforcement, FiscalTenantConfig  # noqa: E402


class ConfigRepo:
    def __init__(self, config):
        self.config = config

    async def get_by_market(self, _market_id):
        return self.config

    async def save(self, config):
        self.config = config
        return config


@pytest.mark.asyncio
async def test_block_cannot_be_requested_before_warn_readiness_is_complete():
    from application.services.fiscal.fiscal_rollout_service import FiscalRolloutService, FiscalRolloutTransitionError

    config = FiscalTenantConfig(market_id=uuid.uuid4())
    service = FiscalRolloutService(ConfigRepo(config), readiness_provider=lambda _market_id: False)

    with pytest.raises(FiscalRolloutTransitionError) as error:
        await service.transition(market_id=config.market_id, requested="block")

    assert error.value.code == "fiscal.rule_enforcement_transition_invalid"
    assert config.fiscal_rule_enforcement is FiscalRuleEnforcement.OFF


@pytest.mark.asyncio
async def test_block_can_roll_back_to_warn_without_mutating_other_fiscal_data():
    from application.services.fiscal.fiscal_rollout_service import FiscalRolloutService

    config = FiscalTenantConfig(
        market_id=uuid.uuid4(), fiscal_rule_enforcement=FiscalRuleEnforcement.BLOCK
    )
    service = FiscalRolloutService(ConfigRepo(config), readiness_provider=lambda _market_id: False)

    result = await service.transition(market_id=config.market_id, requested="warn")

    assert result is FiscalRuleEnforcement.WARN
    assert config.fiscal_rule_enforcement is FiscalRuleEnforcement.WARN
