from abc import ABC, abstractmethod
from typing import List, Optional, Tuple, Dict, Any
import uuid
from datetime import datetime
from domain.identity import User, Market, Plan
from domain.inventory import Product
from domain.sales import Sale, Box, Terminal
from domain.finance import Customer, FinancialTransaction, CustomerLedger
from domain.support import Ticket, TicketStatus
from domain.fiscal import FiscalConfig, Invoice

# --- IDENTITY & ACCESS ---

class UserRepositoryInterface(ABC):
    @abstractmethod
    async def get_by_email(self, email: str) -> Optional[User]: pass
    @abstractmethod
    async def get_by_id(self, user_id: uuid.UUID) -> Optional[User]: pass
    @abstractmethod
    async def save(self, user: User, commit: bool = True) -> User: pass
    @abstractmethod
    async def count_active_users(self) -> int: pass # New for Analytics
    @abstractmethod
    async def count_new_users(self, since: datetime) -> int: pass # New for Analytics

class PlanRepositoryInterface(ABC):
    @abstractmethod
    async def get_by_id(self, plan_id: uuid.UUID) -> Optional[Plan]: pass
    @abstractmethod
    async def list_all(self) -> List[Plan]: pass
    @abstractmethod
    async def save(self, plan: Plan, commit: bool = True) -> Plan: pass
    @abstractmethod
    async def delete(self, plan_id: uuid.UUID, commit: bool = True) -> bool: pass

class MarketRepositoryInterface(ABC):
    @abstractmethod
    async def get_by_id(self, market_id: uuid.UUID) -> Optional[Market]: pass
    @abstractmethod
    async def count_by_owner(self, owner_id: uuid.UUID) -> int: pass
    @abstractmethod
    async def list_by_owner(self, owner_id: uuid.UUID) -> List[Market]: pass
    @abstractmethod
    async def save(self, market: Market, commit: bool = True) -> Market: pass
    @abstractmethod
    async def count_total(self) -> int: pass # New for Analytics

# --- SALES & TERMINALS ---

class TerminalRepositoryInterface(ABC):
    @abstractmethod
    async def get_by_id(self, terminal_id: uuid.UUID) -> Optional[Terminal]: pass
    @abstractmethod
    async def list_by_market(self, market_id: uuid.UUID) -> List[Terminal]: pass
    @abstractmethod
    async def count_by_market(self, market_id: uuid.UUID) -> int: pass
    @abstractmethod
    async def save(self, terminal: Terminal, commit: bool = True) -> Terminal: pass

class BoxRepositoryInterface(ABC):
    @abstractmethod
    async def count_by_market(self, market_id: uuid.UUID) -> int: pass
    @abstractmethod
    async def list_open_by_market(self, market_id: uuid.UUID) -> List[Box]: pass
    @abstractmethod
    async def save(self, box: Box, commit: bool = True) -> Box: pass
    @abstractmethod
    async def get_by_id(self, box_id: uuid.UUID) -> Optional[Box]: pass
    @abstractmethod
    async def get_open_box_by_terminal(self, terminal_id: uuid.UUID) -> Optional[Box]: pass
    @abstractmethod
    async def get_open_box_by_operator(self, operator_id: uuid.UUID) -> Optional[Box]: pass
    @abstractmethod
    async def count_open(self, market_id: uuid.UUID) -> int: pass

# --- INVENTORY ---

class ProductRepositoryInterface(ABC):
    @abstractmethod
    async def get_by_id(self, product_id: uuid.UUID) -> Optional[Product]: pass
    @abstractmethod
    async def get_by_code(self, market_id: uuid.UUID, code: str) -> Optional[Product]: pass
    @abstractmethod
    async def get_by_barcode(self, market_id: uuid.UUID, barcode: str) -> Optional[Product]: pass
    @abstractmethod
    async def list_by_market(self, market_id: uuid.UUID, active_only: bool = True) -> List[Product]: pass
    @abstractmethod
    async def get_delta(self, market_id: uuid.UUID, last_updated: datetime) -> Dict[str, List]: pass
    @abstractmethod
    async def save(self, product: Product, commit: bool = True) -> Product: pass
    @abstractmethod
    async def list_movements(self, market_id: uuid.UUID, product_id: uuid.UUID) -> List[any]: pass # Updated signature
    @abstractmethod
    async def count_low_stock(self, market_id: uuid.UUID, threshold: int = 10) -> int: pass

# --- SALES ---

class SaleRepositoryInterface(ABC):
    @abstractmethod
    async def get_by_id(self, sale_id: uuid.UUID) -> Optional[Sale]: pass
    @abstractmethod
    async def get_by_offline_id(self, market_id: uuid.UUID, offline_id: str) -> Optional[Sale]: pass
    @abstractmethod
    async def save(self, sale: Sale, commit: bool = True) -> Sale: pass
    @abstractmethod
    async def get_daily_stats(self, market_id: uuid.UUID, date_obj) -> dict: pass
    @abstractmethod
    async def list_by_market(self, market_id: uuid.UUID, limit: int = 100, offset: int = 0) -> List[Sale]: pass
    @abstractmethod
    async def get_sales_summary_by_period(self, market_id: uuid.UUID, start_date: datetime, end_date: datetime) -> List[dict]: pass

# --- FINANCE & SUPPORT ---

class CustomerRepositoryInterface(ABC):
    @abstractmethod
    async def get_by_cpf(self, market_id: uuid.UUID, cpf: str) -> Optional[Customer]: pass
    @abstractmethod
    async def get_by_id(self, customer_id: uuid.UUID) -> Optional[Customer]: pass
    @abstractmethod
    async def list_by_market(self, market_id: uuid.UUID, limit: int = 100, offset: int = 0) -> List[Customer]: pass
    @abstractmethod
    async def save(self, customer: Customer, commit: bool = True) -> Customer: pass
    @abstractmethod
    async def get_total_debt(self, market_id: uuid.UUID) -> float: pass
    @abstractmethod
    async def get_ledger(self, customer_id: uuid.UUID, limit: int = 20) -> List[CustomerLedger]: pass

class FinancialTransactionRepositoryInterface(ABC):
    @abstractmethod
    async def save(self, transaction: FinancialTransaction, commit: bool = True) -> FinancialTransaction: pass
    @abstractmethod
    async def get_by_id(self, transaction_id: uuid.UUID) -> Optional[FinancialTransaction]: pass
    @abstractmethod
    async def list_by_market(self, market_id: uuid.UUID, start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[FinancialTransaction]: pass

class TicketRepositoryInterface(ABC):
    @abstractmethod
    async def save(self, ticket: Ticket, commit: bool = True) -> Ticket: pass
    @abstractmethod
    async def list_by_user(self, user_id: uuid.UUID) -> List[Ticket]: pass
    @abstractmethod
    async def list_all(self, status: Optional[TicketStatus] = None) -> List[Ticket]: pass
    @abstractmethod
    async def list_all_enriched(self) -> List[Tuple[Ticket, User]]: pass # NOVO PARA ADMIN DASHBOARD
    @abstractmethod
    async def get_by_id(self, ticket_id: uuid.UUID) -> Optional[Ticket]: pass

# --- FISCAL (UPDATED) ---

class FiscalRepositoryInterface(ABC):
    """Interface legada — mantida para compatibilidade."""
    @abstractmethod
    async def get_config(self, market_id: uuid.UUID) -> Optional[FiscalConfig]: pass
    @abstractmethod
    async def save_config(self, config: FiscalConfig, commit: bool = True) -> FiscalConfig: pass
    @abstractmethod
    async def get_invoice_by_sale(self, sale_id: uuid.UUID) -> Optional[Invoice]: pass
    @abstractmethod
    async def save_invoice(self, invoice: Invoice, commit: bool = True) -> Invoice: pass

class FiscalProviderInterface(ABC):
    """Contrato legado para provedores de emissão."""
    @abstractmethod
    def emit(self, sale_data: dict, config: FiscalConfig) -> Dict[str, Any]:
        pass
