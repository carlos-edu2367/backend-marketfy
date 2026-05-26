"""Política central de autorização por tenant (PR 9 / PR 10 da Fase 3).

Este módulo centraliza a verificação de acesso a uma loja (`market`).
A regra base é owner-only. Depois do PR 10, membros ativos da loja com
role compatível também passam.

A assinatura `resolve_market_access(market_id, user, db, permission)` aceita
um `MarketPermission` opcional. Por enquanto a permissão é apenas validada
contra o mapa role -> permissions, mas o owner sempre é autorizado.
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from domain.identity import UserRole
from infra.repositories.sqlalchemy_repos import SQLAlchemyMarketRepository


class MarketPermission(str, Enum):
    """Permissões granulares aplicadas dentro de uma loja."""

    MARKET_READ = "market.read"
    MARKET_WRITE = "market.write"

    INVENTORY_READ = "inventory.read"
    INVENTORY_WRITE = "inventory.write"

    SALES_READ = "sales.read"
    SALES_WRITE = "sales.write"

    FINANCE_READ = "finance.read"
    FINANCE_WRITE = "finance.write"

    FISCAL_READ = "fiscal.read"
    FISCAL_WRITE = "fiscal.write"

    ANALYTICS_READ = "analytics.read"
    REPORTS_READ = "reports.read"


# Mapeamento role -> permissões. Owner recebe todas implicitamente em
# `resolve_market_access`. Este mapa é usado quando o PR 10 ativa
# membros não-owner (manager, cashier).
ROLE_PERMISSIONS: dict[UserRole, set[MarketPermission]] = {
    UserRole.OWNER: set(MarketPermission),
    UserRole.MANAGER: {
        MarketPermission.MARKET_READ,
        MarketPermission.MARKET_WRITE,
        MarketPermission.INVENTORY_READ,
        MarketPermission.INVENTORY_WRITE,
        MarketPermission.SALES_READ,
        MarketPermission.SALES_WRITE,
        MarketPermission.FINANCE_READ,
        MarketPermission.FINANCE_WRITE,
        MarketPermission.FISCAL_READ,
        MarketPermission.ANALYTICS_READ,
        MarketPermission.REPORTS_READ,
    },
    UserRole.CASHIER: {
        MarketPermission.MARKET_READ,
        MarketPermission.INVENTORY_READ,
        MarketPermission.SALES_READ,
        MarketPermission.SALES_WRITE,
    },
    UserRole.ADMIN: set(),
}


class MarketNotFoundError(Exception):
    """Lança 404 — loja inexistente."""


class MarketAccessDenied(Exception):
    """Lança 403 — usuário não tem acesso ou permissão suficiente."""

    def __init__(self, detail: str = "Acesso negado."):
        super().__init__(detail)
        self.detail = detail


def role_has_permission(role: UserRole, permission: Optional[MarketPermission]) -> bool:
    if permission is None:
        return True
    allowed = ROLE_PERMISSIONS.get(role, set())
    return permission in allowed


async def resolve_market_access(
    market_id: uuid.UUID,
    user,
    db: AsyncSession,
    permission: Optional[MarketPermission] = None,
    owner_only: bool = False,
):
    """Retorna o `Market` se o usuário tem acesso. Caso contrário, levanta exceção.

    Regras:
    - Owner do market sempre passa (com qualquer permissão).
    - Admin SaaS NÃO ganha acesso implícito a dados operacionais; precisa de
      endpoint admin explícito (rotas em /api/v1/admin).
    - Membro ativo da loja (PR 10) passa se seu role tem a permissão pedida,
      a menos que `owner_only=True`.
    """
    market_repo = SQLAlchemyMarketRepository(db)
    market = await market_repo.get_by_id(market_id)
    if not market:
        raise MarketNotFoundError("Loja não encontrada")

    if market.owner_id == user.id:
        return market

    if owner_only:
        raise MarketAccessDenied("Acesso restrito ao proprietário da loja.")

    # PR 10: verifica vínculo em market_members.
    try:
        from infra.repositories.market_member_repo import (
            SQLAlchemyMarketMemberRepository,
        )
    except ImportError:
        raise MarketAccessDenied()

    member_repo = SQLAlchemyMarketMemberRepository(db)
    member = await member_repo.get_active_member(market_id, user.id)
    if member is None:
        raise MarketAccessDenied()

    if not role_has_permission(member.role, permission):
        raise MarketAccessDenied("Permissão insuficiente para este recurso.")

    return market
