from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from jose import JWTError, jwt
import uuid

from infra.database.setup import get_db
from infra.config.settings import get_settings
from infra.security.auth_handler import AuthHandler
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
        customer_repo=SQLAlchemyCustomerRepository(db)
    )

def get_finance_service(db: AsyncSession = Depends(get_db)):
    return FinanceService(
        customer_repo=SQLAlchemyCustomerRepository(db),
        transaction_repo=SQLAlchemyFinancialTransactionRepository(db)
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

# --- PERMISSIONS ---
class PermissionChecker:
    @staticmethod
    async def verify_market_ownership(market_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession):
        repo = SQLAlchemyMarketRepository(db)
        market = await repo.get_by_id(market_id)
        if not market:
            raise HTTPException(status_code=404, detail="Loja não encontrada")
        
        if market.owner_id != user_id:
             # TODO: Implementar verificação de tabela de funcionários (UserMarket)
             raise HTTPException(status_code=403, detail="Acesso negado.")
        
        return market