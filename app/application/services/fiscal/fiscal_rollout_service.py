"""Explicit, reversible fiscal-rule rollout transitions."""
from __future__ import annotations

import inspect

from domain.fiscal import FiscalRuleEnforcement


class FiscalRolloutTransitionError(Exception):
    code = "fiscal.rule_enforcement_transition_invalid"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.code = code or self.code


class FiscalRolloutService:
    def __init__(self, config_repository, readiness_provider):
        self._config_repository = config_repository
        self._readiness_provider = readiness_provider

    async def transition(self, *, market_id, requested: str) -> FiscalRuleEnforcement:
        config = await self._config_repository.get_by_market(market_id)
        if config is None:
            raise FiscalRolloutTransitionError("Configuração fiscal não encontrada.")
        try:
            target = FiscalRuleEnforcement(requested)
        except ValueError as exc:
            raise FiscalRolloutTransitionError("Modo fiscal inválido.") from exc

        current = config.fiscal_rule_enforcement
        if current is FiscalRuleEnforcement.BLOCK and target is FiscalRuleEnforcement.WARN:
            config.fiscal_rule_enforcement = target
        elif current is FiscalRuleEnforcement.OFF and target is FiscalRuleEnforcement.WARN:
            if not await self._is_ready(market_id):
                raise FiscalRolloutTransitionError("O mercado não possui evidência fiscal suficiente para warn.")
            config.fiscal_rule_enforcement = target
        elif current is FiscalRuleEnforcement.WARN and target is FiscalRuleEnforcement.BLOCK:
            if not await self._is_ready(market_id):
                raise FiscalRolloutTransitionError("O mercado não está pronto para block.")
            config.fiscal_rule_enforcement = target
        elif target is current:
            return current
        else:
            raise FiscalRolloutTransitionError("Transição fiscal não permitida.")

        await self._config_repository.save(config)
        return config.fiscal_rule_enforcement

    async def _is_ready(self, market_id) -> bool:
        value = self._readiness_provider(market_id)
        if inspect.isawaitable(value):
            value = await value
        return bool(value)
