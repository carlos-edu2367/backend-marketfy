from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from jose import JWTError, jwt
from typing import Optional, Callable
import uuid

from infra.database.setup import get_db
from infra.config.settings import get_settings
from infra.security.auth_handler import AuthHandler
from infra.security.authorization import require_admin_user
from infra.security.market_access import (
    MarketAccessDenied,
    MarketNotFoundError,
    MarketPermission,
    resolve_market_access,
)
from infra.repositories.sqlalchemy_repos import (
    SQLAlchemyUserRepository, SQLAlchemyProductRepository, SQLAlchemySaleRepository,
    SQLAlchemyMarketRepository, SQLAlchemyBoxRepository, SQLAlchemyPlanRepository,
    SQLAlchemyCustomerRepository, SQLAlchemyTicketRepository, SQLAlchemyTerminalRepository,
    SQLAlchemyFinancialTransactionRepository, SQLAlchemyFiscalRepository
)
# Import do Repositório de Stats
from infra.repositories.admin_stats_repo import AdminStatsRepository
from infra.repositories.analytics_repo import AnalyticsRepository

# Import dos Serviços
from application.services.identity_service import IdentityService
from application.services.sales_service import SalesService
from application.services.inventory_service import InventoryService
from application.services.finance_support import FinanceService, SupportService
from application.services.subscription_service import SubscriptionService
from application.services.admin_dashboard_service import AdminDashboardService
from application.services.admin_service import AdminService
from application.services.fiscal_service import FiscalService
from application.services.analytics_service import AnalyticsService
from application.services.finance_report_service import FinanceReportService # NOVO
from application.services.audit_service import AuditService
from infra.repositories.audit_repo import SQLAlchemyAuditLogRepository
from infra.repositories.fiscal_repo import (
    SQLAlchemyFiscalTenantConfigRepository,
    SQLAlchemyProductTaxRuleRepository,
)
from application.services.fiscal.tax_rule_service import TaxRuleService
from application.services.fiscal.tax_rule_calculator import TaxRuleCalculator

from infra.providers.focus_nfe_provider import FocusNFeProvider

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")
settings = get_settings()

# --- AUTH ---
async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciais inválidas",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = AuthHandler.decode_token(token)
        if payload is None:
            raise credentials_exception
        if payload.get("typ", "access") != "access":
            raise credentials_exception
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    repo = SQLAlchemyUserRepository(db)
    user = await repo.get_by_id(uuid.UUID(user_id))
    if user is None:
        raise credentials_exception
    return user

# --- SERVICES FACTORIES ---

def get_identity_service(db: AsyncSession = Depends(get_db)):
    return IdentityService(
        user_repo=SQLAlchemyUserRepository(db),
        market_repo=SQLAlchemyMarketRepository(db),
        plan_repo=SQLAlchemyPlanRepository(db),
        hasher=AuthHandler.get_password_hash
    )

def get_inventory_service(db: AsyncSession = Depends(get_db)):
    return InventoryService(
        product_repo=SQLAlchemyProductRepository(db),
        market_repo=SQLAlchemyMarketRepository(db)
    )

def get_sales_service(db: AsyncSession = Depends(get_db)):
    return SalesService(
        sale_repo=SQLAlchemySaleRepository(db),
        box_repo=SQLAlchemyBoxRepository(db),
        product_repo=SQLAlchemyProductRepository(db),
        market_repo=SQLAlchemyMarketRepository(db),
        user_repo=SQLAlchemyUserRepository(db),
        terminal_repo=SQLAlchemyTerminalRepository(db),
        plan_repo=SQLAlchemyPlanRepository(db),
        customer_repo=SQLAlchemyCustomerRepository(db),
        financial_repo=SQLAlchemyFinancialTransactionRepository(db),
        fiscal_config_repo=SQLAlchemyFiscalTenantConfigRepository(db),
        tax_rule_service=TaxRuleService(SQLAlchemyProductTaxRuleRepository(db)),
        tax_rule_calculator=TaxRuleCalculator(),
        environment=settings.ENVIRONMENT,
        fiscal_offline_max_age_minutes=settings.FISCAL_OFFLINE_MAX_AGE_MINUTES,
        fiscal_product_rules_enabled=settings.FISCAL_PRODUCT_RULES_ENABLED,
    )

def get_finance_service(db: AsyncSession = Depends(get_db)):
    return FinanceService(
        customer_repo=SQLAlchemyCustomerRepository(db),
        transaction_repo=SQLAlchemyFinancialTransactionRepository(db),
        sale_repo=SQLAlchemySaleRepository(db),
    )

def get_finance_report_service(db: AsyncSession = Depends(get_db)): # NOVO
    return FinanceReportService(
        sale_repo=SQLAlchemySaleRepository(db),
        transaction_repo=SQLAlchemyFinancialTransactionRepository(db),
        market_repo=SQLAlchemyMarketRepository(db)
    )

def get_fiscal_service(db: AsyncSession = Depends(get_db)):
    provider = FocusNFeProvider(api_token=None) 
    return FiscalService(
        fiscal_repo=SQLAlchemyFiscalRepository(db),
        market_repo=SQLAlchemyMarketRepository(db),
        sale_repo=SQLAlchemySaleRepository(db),
        provider=provider
    )

def get_support_service(db: AsyncSession = Depends(get_db)):
    return SupportService(ticket_repo=SQLAlchemyTicketRepository(db))

def get_subscription_service(db: AsyncSession = Depends(get_db)):
    return SubscriptionService(
        user_repo=SQLAlchemyUserRepository(db),
        plan_repo=SQLAlchemyPlanRepository(db)
    )

def get_admin_dashboard_service(db: AsyncSession = Depends(get_db)):
    return AdminDashboardService(
        stats_repo=AdminStatsRepository(db),
        user_repo=SQLAlchemyUserRepository(db),
        plan_repo=SQLAlchemyPlanRepository(db),
        ticket_repo=SQLAlchemyTicketRepository(db),
        hasher=AuthHandler.get_password_hash
    )

def get_admin_plan_service(db: AsyncSession = Depends(get_db)):
    return AdminService(plan_repo=SQLAlchemyPlanRepository(db))

def get_analytics_service(db: AsyncSession = Depends(get_db)):
    return AnalyticsService(analytics_repo=AnalyticsRepository(db))

def get_audit_service(db: AsyncSession = Depends(get_db)):
    return AuditService(SQLAlchemyAuditLogRepository(db))

# --- POLÍTICA CENTRAL DE AUTORIZAÇÃO (Fase 3 / PR 9) ---
#
# A regra é centralizada em `infra/security/market_access.py`. Os helpers
# abaixo expõem o policy como dependências FastAPI reutilizáveis.

class PermissionChecker:
    """Compatibilidade. Usa o policy central por baixo.

    Mantido enquanto os routers migram para `require_market_access` /
    `require_market_owner`. Não adicionar nova lógica aqui.
    """

    @staticmethod
    async def verify_market_ownership(market_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession):
        market_repo = SQLAlchemyMarketRepository(db)
        market = await market_repo.get_by_id(market_id)
        if not market:
            raise HTTPException(status_code=404, detail="Loja não encontrada")

        if market.owner_id != user_id:
            # Após PR 10 a checagem deve considerar market_members.
            # PermissionChecker permanece estrito (owner-only) para não
            # alargar acesso por engano em rotas não migradas.
            raise HTTPException(status_code=403, detail="Acesso negado.")

        return market


def require_admin(current_user = Depends(get_current_user)):
    try:
        return require_admin_user(current_user)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


def _build_market_dependency(
    permission: Optional[MarketPermission],
    owner_only: bool,
    return_access: bool = False,
) -> Callable:
    async def _dep(
        market_id: uuid.UUID,
        current_user=Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        try:
            return await resolve_market_access(
                market_id=market_id,
                user=current_user,
                db=db,
                permission=permission,
                owner_only=owner_only,
                return_access=return_access,
            )
        except MarketNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except MarketAccessDenied as exc:
            raise HTTPException(status_code=403, detail=exc.detail)

    return _dep


def require_market_access(permission: Optional[MarketPermission] = None) -> Callable:
    """Dependência FastAPI que valida acesso à loja para uma permissão.

    Owner sempre passa. Membros (PR 10) passam se o role tem a permissão.
    """
    return _build_market_dependency(permission=permission, owner_only=False)


def require_market_access_context(
    permission: Optional[MarketPermission] = None,
) -> Callable:
    """Return the market plus the active tenant member checked for permission."""
    return _build_market_dependency(
        permission=permission,
        owner_only=False,
        return_access=True,
    )


def require_market_owner() -> Callable:
    """Dependência FastAPI que exige que o usuário seja o owner da loja."""
    return _build_market_dependency(permission=None, owner_only=True)


def get_pix_payment_service(db: AsyncSession = Depends(get_db)):
    from application.services.pix.payment_service import PixPaymentService
    from application.services.pix.connection_service import MercadoPagoConnectionService
    from application.services.pix.completion import SaleCompleter
    from infra.repositories.pix_repo import (
        PixPaymentAttemptRepository, MercadoPagoConnectionRepository,
        MercadoPagoPosRegistrationRepository,
    )
    from infra.repositories.sqlalchemy_repos import (
        SQLAlchemySaleRepository, SQLAlchemyBoxRepository, SQLAlchemyProductRepository,
        SQLAlchemyMarketRepository, SQLAlchemyFinancialTransactionRepository,
    )
    from infra.providers.pix.mercadopago import MercadoPagoPixProvider
    from infra.providers.pix.location import UnconfiguredPosLocationProvider
    from infra.clients.mercadopago_client import MercadoPagoClient
    from infra.cache.redis_lock import RedisLock
    from infra.cache.pix_event_bus import PixEventBus

    conn_service = MercadoPagoConnectionService(
        MercadoPagoConnectionRepository(db), MercadoPagoClient(), RedisLock(),
        pos_repo=MercadoPagoPosRegistrationRepository(db),
    )
    completer = SaleCompleter(
        sale_repo=SQLAlchemySaleRepository(db), product_repo=SQLAlchemyProductRepository(db),
        box_repo=SQLAlchemyBoxRepository(db), financial_repo=SQLAlchemyFinancialTransactionRepository(db),
        payment_repo=SQLAlchemySaleRepository(db))  # add_pix_payment implementado em SQLAlchemySaleRepository
    return PixPaymentService(
        attempt_repo=PixPaymentAttemptRepository(db), sale_repo=SQLAlchemySaleRepository(db),
        box_repo=SQLAlchemyBoxRepository(db), product_repo=SQLAlchemyProductRepository(db),
        connection_service=conn_service, provider=MercadoPagoPixProvider(), lock=RedisLock(),
        market_repo=SQLAlchemyMarketRepository(db),
        pos_location_provider=UnconfiguredPosLocationProvider(),
        completer=completer, event_bus=PixEventBus())
