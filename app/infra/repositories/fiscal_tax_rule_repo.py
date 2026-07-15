"""Fiscal tax rule v2 repository boundary backed by the canonical repository."""

from infra.repositories.fiscal_repo import SQLAlchemyProductTaxRuleRepository

__all__ = ["SQLAlchemyProductTaxRuleRepository"]
