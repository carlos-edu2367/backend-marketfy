"""
ProviderFactory — instancia o provider fiscal correto conforme configuração.

Regras:
- FISCAL_PROVIDER=neectify_fiscal (default) → NeectifyFiscalProvider
- FISCAL_PROVIDER=fake → FakeFiscalProvider (dev/test/staging apenas)
- Em produção, fake é BLOQUEADO para evitar acidente operacional.
"""
from __future__ import annotations

from typing import Optional

from infra.config.logger import get_logger
from infra.providers.fiscal.base import FiscalProviderGateway

logger = get_logger("provider_factory")

_singleton_neectify: Optional["NeectifyFiscalProvider"] = None  # noqa: F821
_singleton_fake: Optional["FakeFiscalProvider"] = None  # noqa: F821


def get_fiscal_provider(
    provider_name: str = "neectify_fiscal",
    environment: str = "homologacao",
    fake_scenario: str = "authorize",
) -> FiscalProviderGateway:
    """
    Retorna a instância correta do provider fiscal.

    O provider é compartilhado como singleton para reutilizar o client HTTP.
    """
    global _singleton_neectify, _singleton_fake

    from infra.config.settings import get_settings
    settings = get_settings()

    if provider_name == "fake":
        if settings.is_production:
            raise RuntimeError(
                "FakeFiscalProvider NÃO pode ser usado em produção. "
                "Configure FISCAL_PROVIDER=neectify_fiscal."
            )
        if _singleton_fake is None:
            from infra.providers.fiscal.fake_provider import FakeFiscalProvider
            _singleton_fake = FakeFiscalProvider(scenario=fake_scenario)
            logger.warning("fiscal_fake_provider_active",
                           extra={"extra_data": {"environment": environment}})
        return _singleton_fake

    # Default: Neectify Fiscal
    if _singleton_neectify is None:
        from infra.clients.neectify_fiscal_client import NeectifyFiscalClient
        from infra.providers.fiscal.neectify_fiscal_provider import NeectifyFiscalProvider
        client = NeectifyFiscalClient(
            api_key=settings.NEECTIFY_API_KEY,
            base_url=settings.NEECTIFY_BASE_URL,
            timeout_seconds=settings.NEECTIFY_TIMEOUT_SECONDS,
        )
        _singleton_neectify = NeectifyFiscalProvider(client=client)
        logger.info("fiscal_neectify_provider_initialized",
                    extra={"extra_data": {"environment": environment}})
    return _singleton_neectify


def reset_provider_singletons():
    """Para uso em testes — reseta singletons."""
    global _singleton_neectify, _singleton_fake
    _singleton_neectify = None
    _singleton_fake = None
