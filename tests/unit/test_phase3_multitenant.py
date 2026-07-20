"""Testes de isolamento multi-tenant (Fase 3 / PR 12).

Cobre a política central em `infra/security/market_access.py`:
- Owner sempre tem acesso à própria loja.
- Proprietário de outra loja é barrado (IDOR).
- Admin SaaS NÃO ganha acesso implícito a dados operacionais.
- Membros ativos passam se o role tem a permissão pedida.
- Membros com permissão insuficiente são barrados.
- Membros inativos são barrados.
- `owner_only=True` barra qualquer não-owner, mesmo manager.
- Loja inexistente levanta `MarketNotFoundError`.
"""

from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.abspath(os.path.join(current_dir, "../../app"))
if app_dir not in sys.path:
    sys.path.append(app_dir)

from domain.identity import CPF, Email, User, UserRole
from infra.security.market_access import (
    ROLE_PERMISSIONS,
    MarketAccessDenied,
    MarketNotFoundError,
    MarketPermission,
    resolve_market_access,
    role_has_permission,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_user(role: UserRole = UserRole.OWNER) -> User:
    user = User(
        name="Test User",
        email=Email(f"{uuid.uuid4()}@test.local"),
        cpf=CPF("12345678900"),
        password_hash="hash",
        role=role,
    )
    return user


@dataclass
class FakeMarket:
    id: uuid.UUID
    owner_id: uuid.UUID
    name: str = "Loja Fake"
    is_active: bool = True


@dataclass
class FakeMember:
    id: uuid.UUID
    market_id: uuid.UUID
    user_id: uuid.UUID
    role: UserRole
    is_active: bool
    created_at: datetime = None
    updated_at: datetime = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow()
        if self.updated_at is None:
            self.updated_at = datetime.utcnow()


def _patch_repos(market: Optional[FakeMarket], member: Optional[FakeMember]):
    """Retorna um context manager que injeta repos fake no market_access."""
    import infra.security.market_access as ma_module

    fake_market_repo = AsyncMock()
    fake_market_repo.get_by_id.return_value = market

    fake_member_repo = AsyncMock()
    fake_member_repo.get_active_member.return_value = member

    from unittest.mock import patch

    return patch.multiple(
        "infra.security.market_access",
        SQLAlchemyMarketRepository=MagicMock(return_value=fake_market_repo),
        **{"infra.repositories.market_member_repo.SQLAlchemyMarketMemberRepository": MagicMock(return_value=fake_member_repo)},
    )


async def _resolve(
    market: Optional[FakeMarket],
    user: User,
    member: Optional[FakeMember] = None,
    permission: Optional[MarketPermission] = None,
    owner_only: bool = False,
):
    """Executa resolve_market_access com repos fake injetados."""
    from unittest.mock import patch, AsyncMock, MagicMock

    fake_market_repo = AsyncMock()
    fake_market_repo.get_by_id.return_value = market

    fake_member_repo = AsyncMock()
    fake_member_repo.get_active_member.return_value = member

    with patch(
        "infra.security.market_access.SQLAlchemyMarketRepository",
        return_value=fake_market_repo,
    ), patch(
        "infra.repositories.market_member_repo.SQLAlchemyMarketMemberRepository",
        return_value=fake_member_repo,
    ):
        # Patcha o import lazy dentro de resolve_market_access
        import infra.repositories.market_member_repo as mmr_module
        original_cls = mmr_module.SQLAlchemyMarketMemberRepository

        mmr_module.SQLAlchemyMarketMemberRepository = MagicMock(return_value=fake_member_repo)
        try:
            market_id = market.id if market else uuid.uuid4()
            return await resolve_market_access(
                market_id=market_id,
                user=user,
                db=None,
                permission=permission,
                owner_only=owner_only,
            )
        finally:
            mmr_module.SQLAlchemyMarketMemberRepository = original_cls


# ---------------------------------------------------------------------------
# Testes: role_has_permission (puro, sem I/O)
# ---------------------------------------------------------------------------

def test_owner_has_all_permissions():
    for perm in MarketPermission:
        assert role_has_permission(UserRole.OWNER, perm) is True


def test_manager_has_fiscal_write():
    assert role_has_permission(UserRole.MANAGER, MarketPermission.FISCAL_WRITE) is True


def test_manager_has_fiscal_read():
    assert role_has_permission(UserRole.MANAGER, MarketPermission.FISCAL_READ) is True


def test_cashier_has_sales_write():
    assert role_has_permission(UserRole.CASHIER, MarketPermission.SALES_WRITE) is True


def test_cashier_has_no_finance_read():
    assert role_has_permission(UserRole.CASHIER, MarketPermission.FINANCE_READ) is False


def test_cashier_has_no_inventory_write():
    assert role_has_permission(UserRole.CASHIER, MarketPermission.INVENTORY_WRITE) is False


def test_admin_has_no_market_permissions():
    for perm in MarketPermission:
        assert role_has_permission(UserRole.ADMIN, perm) is False


@pytest.mark.parametrize(
    "role,perm,expected",
    [
        (UserRole.OWNER, MarketPermission.FISCAL_WRITE, True),
        (UserRole.MANAGER, MarketPermission.FISCAL_WRITE, True),
        (UserRole.MANAGER, MarketPermission.ANALYTICS_READ, True),
        (UserRole.CASHIER, MarketPermission.SALES_READ, True),
        (UserRole.CASHIER, MarketPermission.REPORTS_READ, False),
        (UserRole.ADMIN, MarketPermission.MARKET_READ, False),
    ],
)
def test_role_permission_matrix(role, perm, expected):
    assert role_has_permission(role, perm) is expected


def test_role_has_permission_returns_true_for_none_permission():
    """Sem permissão especificada, qualquer role passa (checagem de existência apenas)."""
    for role in UserRole:
        assert role_has_permission(role, None) is True


# ---------------------------------------------------------------------------
# Testes: resolve_market_access (com repos fake)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_owner_always_has_access():
    owner = make_user(UserRole.OWNER)
    market = FakeMarket(id=uuid.uuid4(), owner_id=owner.id)

    result = await _resolve(market, owner, permission=MarketPermission.FINANCE_WRITE)

    assert result is market


@pytest.mark.asyncio
async def test_market_not_found_raises_404():
    user = make_user(UserRole.OWNER)

    with pytest.raises(MarketNotFoundError):
        await _resolve(market=None, user=user)


@pytest.mark.asyncio
async def test_idor_prevention_different_owner_denied():
    """Owner da loja A não pode acessar loja B (IDOR)."""
    owner_a = make_user(UserRole.OWNER)
    owner_b = make_user(UserRole.OWNER)
    market_b = FakeMarket(id=uuid.uuid4(), owner_id=owner_b.id)

    with pytest.raises(MarketAccessDenied):
        # owner_a tenta acessar market_b sem ser membro
        await _resolve(market_b, owner_a, member=None)


@pytest.mark.asyncio
async def test_admin_saas_cannot_access_market_data():
    """Admin SaaS não tem acesso implícito a dados operacionais de loja."""
    admin = make_user(UserRole.ADMIN)
    market = FakeMarket(id=uuid.uuid4(), owner_id=uuid.uuid4())  # owned by someone else

    with pytest.raises(MarketAccessDenied):
        await _resolve(market, admin, member=None)


@pytest.mark.asyncio
async def test_active_manager_with_permission_passes():
    manager = make_user(UserRole.MANAGER)
    market = FakeMarket(id=uuid.uuid4(), owner_id=uuid.uuid4())
    member = FakeMember(
        id=uuid.uuid4(),
        market_id=market.id,
        user_id=manager.id,
        role=UserRole.MANAGER,
        is_active=True,
    )

    result = await _resolve(market, manager, member=member, permission=MarketPermission.SALES_READ)

    assert result is market


@pytest.mark.asyncio
async def test_active_cashier_with_sales_write_passes():
    cashier = make_user(UserRole.CASHIER)
    market = FakeMarket(id=uuid.uuid4(), owner_id=uuid.uuid4())
    member = FakeMember(
        id=uuid.uuid4(),
        market_id=market.id,
        user_id=cashier.id,
        role=UserRole.CASHIER,
        is_active=True,
    )

    result = await _resolve(market, cashier, member=member, permission=MarketPermission.SALES_WRITE)

    assert result is market


@pytest.mark.asyncio
async def test_cashier_denied_finance_read():
    """Caixa não pode ler dados financeiros (permissão insuficiente)."""
    cashier = make_user(UserRole.CASHIER)
    market = FakeMarket(id=uuid.uuid4(), owner_id=uuid.uuid4())
    member = FakeMember(
        id=uuid.uuid4(),
        market_id=market.id,
        user_id=cashier.id,
        role=UserRole.CASHIER,
        is_active=True,
    )

    with pytest.raises(MarketAccessDenied):
        await _resolve(market, cashier, member=member, permission=MarketPermission.FINANCE_READ)


@pytest.mark.asyncio
async def test_inactive_member_is_denied():
    """Membro desativado não tem acesso."""
    manager = make_user(UserRole.MANAGER)
    market = FakeMarket(id=uuid.uuid4(), owner_id=uuid.uuid4())
    # Simula repositório que retorna None (is_active=False filtra na query)
    # get_active_member retorna None para membros inativos

    with pytest.raises(MarketAccessDenied):
        await _resolve(market, manager, member=None)


@pytest.mark.asyncio
async def test_owner_only_blocks_active_manager():
    """owner_only=True deve barrar managers ativos."""
    manager = make_user(UserRole.MANAGER)
    market = FakeMarket(id=uuid.uuid4(), owner_id=uuid.uuid4())
    member = FakeMember(
        id=uuid.uuid4(),
        market_id=market.id,
        user_id=manager.id,
        role=UserRole.MANAGER,
        is_active=True,
    )

    with pytest.raises(MarketAccessDenied, match="proprietário"):
        await _resolve(market, manager, member=member, owner_only=True)


@pytest.mark.asyncio
async def test_owner_only_passes_for_actual_owner():
    owner = make_user(UserRole.OWNER)
    market = FakeMarket(id=uuid.uuid4(), owner_id=owner.id)

    result = await _resolve(market, owner, owner_only=True)

    assert result is market


@pytest.mark.asyncio
async def test_active_manager_has_fiscal_write():
    """Manager ativo recebe fiscal.write no seu vínculo de mercado."""
    manager = make_user(UserRole.MANAGER)
    market = FakeMarket(id=uuid.uuid4(), owner_id=uuid.uuid4())
    member = FakeMember(
        id=uuid.uuid4(),
        market_id=market.id,
        user_id=manager.id,
        role=UserRole.MANAGER,
        is_active=True,
    )

    result = await _resolve(
        market, manager, member=member, permission=MarketPermission.FISCAL_WRITE
    )
    assert result is market


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,perm,should_pass",
    [
        (UserRole.MANAGER, MarketPermission.INVENTORY_WRITE, True),
        (UserRole.MANAGER, MarketPermission.FINANCE_WRITE, True),
        (UserRole.MANAGER, MarketPermission.FISCAL_WRITE, True),
        (UserRole.CASHIER, MarketPermission.MARKET_READ, True),
        (UserRole.CASHIER, MarketPermission.INVENTORY_READ, True),
        (UserRole.CASHIER, MarketPermission.INVENTORY_WRITE, False),
        (UserRole.CASHIER, MarketPermission.FINANCE_WRITE, False),
        (UserRole.CASHIER, MarketPermission.ANALYTICS_READ, False),
    ],
)
async def test_member_permission_matrix(role, perm, should_pass):
    user = make_user(role)
    market = FakeMarket(id=uuid.uuid4(), owner_id=uuid.uuid4())
    member = FakeMember(
        id=uuid.uuid4(),
        market_id=market.id,
        user_id=user.id,
        role=role,
        is_active=True,
    )

    if should_pass:
        result = await _resolve(market, user, member=member, permission=perm)
        assert result is market
    else:
        with pytest.raises(MarketAccessDenied):
            await _resolve(market, user, member=member, permission=perm)


# ---------------------------------------------------------------------------
# Testes: MarketMember entity (sem I/O)
# ---------------------------------------------------------------------------

def test_market_member_entity_creation():
    from infra.repositories.market_member_repo import MarketMember

    mid = uuid.uuid4()
    uid = uuid.uuid4()
    now = datetime.utcnow()
    member = MarketMember(
        id=uuid.uuid4(),
        market_id=mid,
        user_id=uid,
        role=UserRole.MANAGER,
        is_active=True,
        created_at=now,
        updated_at=now,
    )

    assert member.market_id == mid
    assert member.user_id == uid
    assert member.role == UserRole.MANAGER
    assert member.is_active is True


# ---------------------------------------------------------------------------
# Testes: ROLE_PERMISSIONS completeness
# ---------------------------------------------------------------------------

def test_owner_has_all_permissions_in_map():
    owner_perms = ROLE_PERMISSIONS[UserRole.OWNER]
    assert owner_perms == set(MarketPermission)


def test_admin_has_empty_permissions_in_map():
    admin_perms = ROLE_PERMISSIONS[UserRole.ADMIN]
    assert admin_perms == set()


def test_manager_has_all_market_permissions():
    manager_perms = ROLE_PERMISSIONS[UserRole.MANAGER]
    all_perms = set(MarketPermission)
    missing = all_perms - manager_perms
    assert missing == set()


def test_cashier_permissions_minimal():
    cashier_perms = ROLE_PERMISSIONS[UserRole.CASHIER]
    expected = {
        MarketPermission.MARKET_READ,
        MarketPermission.INVENTORY_READ,
        MarketPermission.SALES_READ,
        MarketPermission.SALES_WRITE,
    }
    assert cashier_perms == expected
