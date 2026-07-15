import json
import uuid
from typing import Optional, List, Tuple, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, cast, Date, extract
from sqlalchemy.orm import selectinload
from decimal import Decimal
from datetime import datetime

# Domain Interfaces & Entities
from domain.interfaces import (
    SaleRepositoryInterface, ProductRepositoryInterface, UserRepositoryInterface,
    MarketRepositoryInterface, BoxRepositoryInterface, PlanRepositoryInterface,
    CustomerRepositoryInterface, TicketRepositoryInterface, TerminalRepositoryInterface,
    FinancialTransactionRepositoryInterface, FiscalRepositoryInterface
)
from domain.sales import Sale, SaleItem, SaleStatus, Payment, Box, BoxStatus, PaymentMethod, Terminal
from domain.inventory import Product, StockMovement, StockMovementType
from domain.identity import User, Email, CPF, UserRole, Market, CNPJ, Plan, PlanType
from domain.finance import Customer, FinancialTransaction, CustomerStatus, TransactionType, CustomerLedger, LedgerType
from domain.support import Ticket, TicketStatus, TicketPriority, TicketMessage
from domain.fiscal import FiscalConfig, Invoice, FiscalEnvironment, InvoiceStatus
from application.services.fiscal.snapshot_integrity import canonical_fiscal_snapshot_json

# Infra Models
from infra.database.models import (
    SaleModel, SaleItemModel, PaymentModel, ProductModel, UserModel, 
    MarketModel, BoxModel, PlanModel, CustomerModel, TicketModel, 
    TicketMessageModel, TerminalModel, FiscalConfigModel, InvoiceModel,
    CustomerLedgerModel, StockMovementModel, FinancialTransactionModel
)

# ==================================================================================
# USER REPOSITORY
# ==================================================================================
class SQLAlchemyUserRepository(UserRepositoryInterface):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_email(self, email: str) -> Optional[User]:
        result = await self.session.execute(select(UserModel).where(UserModel.email == email))
        model = result.scalars().first()
        return self._to_entity(model) if model else None

    async def get_by_id(self, user_id: uuid.UUID) -> Optional[User]:
        model = await self.session.get(UserModel, user_id)
        return self._to_entity(model) if model else None

    async def save(self, user: User, commit: bool = True) -> User:
        model = await self.session.get(UserModel, user.id)
        if not model:
            model = UserModel(id=user.id)
            self.session.add(model)
        
        model.name = user.name
        model.email = user.email.value
        model.cpf = str(user.cpf)
        model.password_hash = user.password_hash
        model.role = user.role.value if hasattr(user.role, 'value') else user.role
        model.is_active = user.is_active
        model.plan_id = user.plan_id
        model.plan_expiration = user.plan_expiration
        model.asaas_customer_id = user.asaas_customer_id
        
        if commit:
            await self.session.commit()
        return user

    async def count_active_users(self) -> int:
        query = select(func.count(UserModel.id)).where(UserModel.is_active == True)
        result = await self.session.execute(query)
        return result.scalar() or 0

    async def count_new_users(self, since: datetime) -> int:
        query = select(func.count(UserModel.id)).where(UserModel.created_at >= since)
        result = await self.session.execute(query)
        return result.scalar() or 0

    async def update_asaas_customer_id(self, user_id: uuid.UUID, asaas_customer_id: str, commit: bool = True) -> None:
        model = await self.session.get(UserModel, user_id)
        if model:
            model.asaas_customer_id = asaas_customer_id
            if commit:
                await self.session.commit()

    def _to_entity(self, m: UserModel) -> User:
        u = User(
            name=m.name,
            email=Email(m.email),
            cpf=CPF(m.cpf),
            password_hash=m.password_hash,
            role=UserRole(m.role),
            is_active=m.is_active,
            plan_id=m.plan_id,
            plan_expiration=m.plan_expiration,
            asaas_customer_id=m.asaas_customer_id
        )
        u.id = m.id
        u.created_at = m.created_at
        return u


# ==================================================================================
# PLAN REPOSITORY
# ==================================================================================
class SQLAlchemyPlanRepository(PlanRepositoryInterface):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, plan_id: uuid.UUID) -> Optional[Plan]:
        model = await self.session.get(PlanModel, plan_id)
        return self._to_entity(model) if model else None

    async def list_all(self) -> List[Plan]:
        result = await self.session.execute(select(PlanModel))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def save(self, plan: Plan, commit: bool = True) -> Plan:
        model = await self.session.get(PlanModel, plan.id)
        if not model:
            model = PlanModel(id=plan.id)
            self.session.add(model)
            
        model.name = plan.name
        model.type = plan.type.value
        model.max_markets = plan.max_markets
        model.max_terminals = plan.max_terminals
        model.price_monthly = plan.price_monthly
        model.price_180days = plan.price_180days
        model.price_annual = plan.price_annual
        model.is_active = plan.is_active
        
        if commit:
            await self.session.commit()
        return plan

    async def delete(self, plan_id: uuid.UUID, commit: bool = True) -> bool:
        model = await self.session.get(PlanModel, plan_id)
        if model:
            await self.session.delete(model)
            if commit: await self.session.commit()
            return True
        return False

    def _to_entity(self, m: PlanModel) -> Plan:
        p = Plan(
            name=m.name,
            type=PlanType(m.type),
            max_markets=m.max_markets,
            max_terminals=m.max_terminals,
            price_monthly=m.price_monthly,
            price_180days=m.price_180days,
            price_annual=m.price_annual,
            fiscal_monthly_limit=getattr(m, "fiscal_monthly_limit", 0) or 0,
            is_active=m.is_active,
        )
        p.id = m.id
        return p

# ==================================================================================
# MARKET REPOSITORY
# ==================================================================================
class SQLAlchemyMarketRepository(MarketRepositoryInterface):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, market_id: uuid.UUID) -> Optional[Market]:
        model = await self.session.get(MarketModel, market_id)
        return self._to_entity(model) if model else None

    async def list_by_owner(self, owner_id: uuid.UUID) -> List[Market]:
        result = await self.session.execute(select(MarketModel).where(MarketModel.owner_id == owner_id))
        return [self._to_entity(m) for m in result.scalars().all()]

    async def count_by_owner(self, owner_id: uuid.UUID) -> int:
        query = select(func.count(MarketModel.id)).where(MarketModel.owner_id == owner_id)
        result = await self.session.execute(query)
        return result.scalar() or 0

    async def count_total(self) -> int:
        query = select(func.count(MarketModel.id))
        result = await self.session.execute(query)
        return result.scalar() or 0

    async def save(self, market: Market, commit: bool = True) -> Market:
        model = await self.session.get(MarketModel, market.id)
        if not model:
            model = MarketModel(id=market.id)
            self.session.add(model)
        
        model.owner_id = market.owner_id
        model.name = market.name
        model.document = market.document.value
        model.address = market.address
        model.is_active = market.active
        
        if commit:
            await self.session.commit()
        return market

    def _to_entity(self, m: MarketModel) -> Market:
        market = Market(
            owner_id=m.owner_id,
            name=m.name,
            document=CNPJ(m.document),
            address=m.address,
            active=m.is_active
        )
        market.id = m.id
        market.created_at = m.created_at
        return market

# ==================================================================================
# PRODUCT REPOSITORY
# ==================================================================================
class SQLAlchemyProductRepository(ProductRepositoryInterface):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(self, product: Product, commit: bool = True) -> Product:
        model = await self.session.get(ProductModel, product.id)
        if not model:
            model = ProductModel(id=product.id)
            self.session.add(model)
        
        model.market_id = product.market_id
        model.name = product.name
        model.code = product.code
        model.barcode = product.barcode
        model.price = product.price
        model.cost_price = product.cost_price
        model.current_stock = product.current_stock
        model.active = product.active
        model.ncm = product.ncm
        model.tax_rule_id = product.tax_rule_id
        model.updated_at = datetime.utcnow()
        if product.deleted_at:
             model.deleted_at = product.deleted_at
        
        # Persiste movimentações pendentes
        if hasattr(product, '_pending_movements') and product._pending_movements:
            for mov in product._pending_movements:
                mov_model = StockMovementModel(
                    id=mov.id,
                    product_id=mov.product_id,
                    movement_type=mov.movement_type.value,
                    quantity=mov.quantity,
                    cost_price=mov.cost_price, 
                    reason=mov.reason,
                    created_at=mov.created_at
                )
                self.session.add(mov_model)
            product._pending_movements = []

        if commit:
            await self.session.commit()
        return product

    async def get_by_id(self, product_id: uuid.UUID) -> Optional[Product]:
        model = await self.session.get(ProductModel, product_id)
        return self._to_entity(model) if model else None

    async def get_by_code(self, market_id: uuid.UUID, code: str) -> Optional[Product]:
        query = select(ProductModel).where(ProductModel.market_id == market_id, ProductModel.code == code)
        result = await self.session.execute(query)
        model = result.scalars().first()
        return self._to_entity(model) if model else None

    async def get_by_barcode(self, market_id: uuid.UUID, barcode: str) -> Optional[Product]:
        query = select(ProductModel).where(ProductModel.market_id == market_id, ProductModel.barcode == barcode)
        result = await self.session.execute(query)
        model = result.scalars().first()
        return self._to_entity(model) if model else None

    async def list_by_market(self, market_id: uuid.UUID, active_only: bool = True) -> List[Product]:
        query = select(ProductModel).where(ProductModel.market_id == market_id)
        if active_only:
             query = query.where(ProductModel.active == True, ProductModel.deleted_at == None)
        result = await self.session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]
    
    async def list_movements(self, market_id: uuid.UUID, product_id: uuid.UUID) -> List[StockMovement]:
        query = select(StockMovementModel).where(
            StockMovementModel.product_id == product_id
        ).order_by(desc(StockMovementModel.created_at))
        
        result = await self.session.execute(query)
        return [
            StockMovement(
                product_id=m.product_id,
                movement_type=StockMovementType(m.movement_type),
                quantity=m.quantity,
                cost_price=m.cost_price,
                reason=m.reason,
                id=m.id,
                created_at=m.created_at
            ) for m in result.scalars().all()
        ]

    async def get_delta(self, market_id: uuid.UUID, since: datetime) -> dict:
        query_updated = select(ProductModel).where(
            ProductModel.market_id == market_id,
            ProductModel.updated_at > since,
            ProductModel.deleted_at == None
        )
        query_deleted = select(ProductModel.id).where(
            ProductModel.market_id == market_id,
            ProductModel.deleted_at > since
        )
        res_upd = await self.session.execute(query_updated)
        res_del = await self.session.execute(query_deleted)
        return {
            "updated": [self._to_entity(m) for m in res_upd.scalars().all()],
            "deleted": res_del.scalars().all()
        }

    async def count_low_stock(self, market_id: uuid.UUID, threshold: int = 10) -> int:
        query = select(func.count(ProductModel.id)).where(
            ProductModel.market_id == market_id,
            ProductModel.current_stock < threshold,
            ProductModel.active == True
        )
        res = await self.session.execute(query)
        return res.scalar() or 0

    def _to_entity(self, m: ProductModel) -> Product:
        p = Product(
            market_id=m.market_id,
            name=m.name,
            code=m.code,
            barcode=m.barcode,
            price=m.price,
            cost_price=m.cost_price,
            current_stock=m.current_stock,
            active=m.active,
            ncm=m.ncm,
            origin=m.origin,
            tax_rule_id=m.tax_rule_id,
        )
        p.id = m.id
        p.created_at = m.created_at
        p.updated_at = m.updated_at
        p.deleted_at = m.deleted_at
        return p

# ==================================================================================
# TERMINAL REPOSITORY
# ==================================================================================
class SQLAlchemyTerminalRepository(TerminalRepositoryInterface):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(self, terminal: Terminal, commit: bool = True) -> Terminal:
        model = await self.session.get(TerminalModel, terminal.id)
        if not model:
            model = TerminalModel(id=terminal.id)
            self.session.add(model)
        
        model.market_id = terminal.market_id
        model.name = terminal.name
        model.active = terminal.active
        
        if commit:
            await self.session.commit()
        return terminal
    
    async def get_by_id(self, terminal_id: uuid.UUID) -> Optional[Terminal]:
        model = await self.session.get(TerminalModel, terminal_id)
        if not model: return None
        t = Terminal(market_id=model.market_id, name=model.name, active=model.active)
        t.id = model.id
        return t

    async def list_by_market(self, market_id: uuid.UUID) -> List[Terminal]:
        result = await self.session.execute(select(TerminalModel).where(TerminalModel.market_id == market_id))
        return [Terminal(market_id=m.market_id, name=m.name, active=m.active, id=m.id) for m in result.scalars().all()]
    
    async def count_by_market(self, market_id: uuid.UUID) -> int:
        res = await self.session.execute(select(func.count(TerminalModel.id)).where(TerminalModel.market_id == market_id))
        return res.scalar() or 0

# ==================================================================================
# BOX REPOSITORY
# ==================================================================================
class SQLAlchemyBoxRepository(BoxRepositoryInterface):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(self, box: Box, commit: bool = True) -> Box:
        model = await self.session.get(BoxModel, box.id)
        if not model:
            model = BoxModel(id=box.id)
            self.session.add(model)
            
        model.market_id = box.market_id
        model.operator_id = box.operator_id
        model.terminal_id = box.terminal_id
        model.status = box.status.value
        model.opened_at = box.opened_at
        model.closed_at = box.closed_at
        model.initial_balance = box.initial_balance
        model.current_balance = box.current_balance
        model.final_balance_reported = box.final_balance_reported
        model.difference = box.difference
        model.closing_observation = box.closing_observation

        if commit:
            await self.session.commit()
        return box

    async def get_by_id(self, box_id: uuid.UUID) -> Optional[Box]:
        model = await self.session.get(BoxModel, box_id)
        return self._to_entity(model) if model else None

    async def get_open_box_by_operator(self, operator_id: uuid.UUID) -> Optional[Box]:
        query = select(BoxModel).where(
            BoxModel.operator_id == operator_id,
            BoxModel.status == 'aberto'
        )
        result = await self.session.execute(query)
        model = result.scalars().first()
        return self._to_entity(model) if model else None
        
    async def get_open_box_by_terminal(self, terminal_id: uuid.UUID) -> Optional[Box]:
        query = select(BoxModel).where(
            BoxModel.terminal_id == terminal_id,
            BoxModel.status == 'aberto'
        )
        result = await self.session.execute(query)
        model = result.scalars().first()
        return self._to_entity(model) if model else None

    async def count_open(self, market_id: uuid.UUID) -> int:
        query = select(func.count(BoxModel.id)).where(
            BoxModel.market_id == market_id,
            BoxModel.status == 'aberto'
        )
        res = await self.session.execute(query)
        return res.scalar() or 0

    async def count_by_market(self, market_id: uuid.UUID) -> int:
        query = select(func.count(BoxModel.id)).where(BoxModel.market_id == market_id)
        res = await self.session.execute(query)
        return res.scalar() or 0

    async def list_open_by_market(self, market_id: uuid.UUID) -> List[Box]:
        query = select(BoxModel).where(
            BoxModel.market_id == market_id,
            BoxModel.status == 'aberto'
        )
        result = await self.session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    def _to_entity(self, m: BoxModel) -> Box:
        b = Box(
            market_id=m.market_id,
            operator_id=m.operator_id,
            terminal_id=m.terminal_id,
            status=BoxStatus(m.status),
            opened_at=m.opened_at,
            initial_balance=m.initial_balance,
            current_balance=m.current_balance
        )
        b.id = m.id
        b.closed_at = m.closed_at
        b.final_balance_reported = m.final_balance_reported
        b.difference = m.difference
        b.closing_observation = m.closing_observation
        return b

# ==================================================================================
# SALE REPOSITORY
# ==================================================================================
class SQLAlchemySaleRepository(SaleRepositoryInterface):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, sale_id: uuid.UUID) -> Optional[Sale]:
        stmt = select(SaleModel).options(
            selectinload(SaleModel.items),
            selectinload(SaleModel.payments),
            selectinload(SaleModel.fiscal_documents)
        ).where(SaleModel.id == sale_id)
        
        res = await self.session.execute(stmt)
        model = res.scalars().first()
        return self._to_entity(model) if model else None

    async def get_by_offline_id(self, market_id: uuid.UUID, offline_id: str) -> Optional[Sale]:
        stmt = select(SaleModel).options(
            selectinload(SaleModel.items),
            selectinload(SaleModel.payments),
            selectinload(SaleModel.fiscal_documents)
        ).where(
            SaleModel.market_id == market_id,
            SaleModel.offline_id == offline_id,
        )
        res = await self.session.execute(stmt)
        model = res.scalars().first()
        return self._to_entity(model) if model else None

    async def save(self, sale: Sale, commit: bool = True) -> Sale:
        model = await self._get_model_by_id(sale.id)
        is_new = False
        if not model:
            is_new = True
            model = SaleModel(id=sale.id, market_id=sale.market_id)
            self.session.add(model)
        
        model.box_id = sale.box_id
        model.operator_id = sale.operator_id
        model.status = sale.status.value
        model.total_amount = sale.total_amount
        model.discount = sale.discount
        model.acrescimo = sale.acrescimo
        
        # Garante que o CPF do cliente seja salvo corretamente
        # Se for uma atualização e o sale.customer_cpf vier vazio, mantemos o que estava (opcional)
        # Mas para garantir consistência com o objeto de domínio, atribuímos direto.
        model.customer_cpf = sale.customer_cpf 
        
        model.offline_id = sale.offline_id
        
        # --- FIX: Correção para AsyncPG (Naive vs Aware Datetime) ---
        # Removemos o tzinfo explicitamente para garantir compatibilidade 
        # com colunas TIMESTAMP WITHOUT TIME ZONE do PostgreSQL
        if sale.synced_at and sale.synced_at.tzinfo:
            model.synced_at = sale.synced_at.replace(tzinfo=None)
        else:
            model.synced_at = sale.synced_at
        if sale.received_at and sale.received_at.tzinfo:
            model.received_at = sale.received_at.replace(tzinfo=None)
        else:
            model.received_at = sale.received_at
            
        if sale.created_at and sale.created_at.tzinfo:
            model.created_at = sale.created_at.replace(tzinfo=None)
        else:
            model.created_at = sale.created_at 
        # -----------------------------------------------------------

        # Itens (Simplificado: Recria tudo ou Adiciona novos. No MVP, venda é imutável após criada, salvo status)
        if is_new:
            for item in sale.items:
                i_model = SaleItemModel(
                    id=item.id,
                    sale_id=sale.id,
                    product_id=item.product_id,
                    product_name_snapshot=item.product_name,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    total=item.total,
                    ncm_snapshot=item.ncm_snapshot,
                    origin_snapshot=item.origin_snapshot,
                    fiscal_tax_snapshot_json=_serialize_fiscal_tax_snapshot(item.fiscal_tax_snapshot),
                    tax_rule_version_snapshot=item.tax_rule_version_snapshot,
                    snapshot_sha256=item.snapshot_sha256,
                    fiscal_calculation_version=item.fiscal_calculation_version,
                )
                self.session.add(i_model)
            
            for pay in sale.payments:
                p_model = PaymentModel(
                    id=pay.id,
                    sale_id=sale.id,
                    method=pay.method.value,
                    amount=pay.amount,
                    installments=pay.installments
                )
                self.session.add(p_model)
        
        if commit:
            await self.session.commit()
            await self.session.refresh(model)
        
        return sale

    async def get_daily_stats(self, market_id: uuid.UUID, date_obj) -> dict:
        stmt = select(
            func.sum(SaleModel.total_amount).label('total'),
            func.count(SaleModel.id).label('count')
        ).where(
            SaleModel.market_id == market_id,
            cast(SaleModel.created_at, Date) == date_obj,
            SaleModel.status == 'concluida'
        )
        res = await self.session.execute(stmt)
        row = res.first()
        return {"total": row.total or Decimal("0.00"), "count": row.count or 0}

    async def list_by_market(self, market_id: uuid.UUID, limit: int = 100, offset: int = 0) -> List[Sale]:
        stmt = select(SaleModel).options(
            selectinload(SaleModel.items),
            selectinload(SaleModel.payments),
            selectinload(SaleModel.fiscal_documents)
        ).where(SaleModel.market_id == market_id).order_by(desc(SaleModel.created_at)).limit(limit).offset(offset)
        
        res = await self.session.execute(stmt)
        return [self._to_entity(m) for m in res.scalars().all()]

    async def get_sales_summary_by_period(self, market_id: uuid.UUID, start_date: datetime, end_date: datetime) -> List[dict]:
        stmt = select(
            cast(SaleModel.created_at, Date).label('date'),
            func.sum(SaleModel.total_amount).label('total'),
            func.count(SaleModel.id).label('count')
        ).where(
            SaleModel.market_id == market_id,
            cast(SaleModel.created_at, Date) >= start_date,
            cast(SaleModel.created_at, Date) <= end_date,
            SaleModel.status == 'concluida'
        ).group_by(cast(SaleModel.created_at, Date)).order_by('date')
        
        res = await self.session.execute(stmt)
        return [{"date": row.date, "total": row.total, "count": row.count} for row in res.all()]

    async def _get_model_by_id(self, sale_id: uuid.UUID) -> Optional[SaleModel]:
        res = await self.session.execute(select(SaleModel).where(SaleModel.id == sale_id))
        return res.scalars().first()

    def _to_entity(self, m: SaleModel) -> Sale:
        s = Sale(
            market_id=m.market_id,
            box_id=m.box_id,
            operator_id=m.operator_id,
            status=SaleStatus(m.status),
            total_amount=m.total_amount,
            discount=m.discount,
            acrescimo=m.acrescimo,
            customer_cpf=m.customer_cpf,
            offline_id=m.offline_id,
            synced_at=m.synced_at,
            received_at=m.received_at,
        )
        s.id = m.id
        s.created_at = m.created_at
        
        # Reconstrói itens
        if m.items:
            for i in m.items:
                s.items.append(SaleItem(
                    sale_id=s.id,
                    product_id=i.product_id,
                    product_name=i.product_name_snapshot,
                    quantity=i.quantity,
                    unit_price=i.unit_price,
                    total=i.total,
                    ncm_snapshot=i.ncm_snapshot,
                    origin_snapshot=i.origin_snapshot,
                    fiscal_tax_snapshot=_deserialize_fiscal_tax_snapshot(i.fiscal_tax_snapshot_json),
                    tax_rule_version_snapshot=i.tax_rule_version_snapshot,
                    snapshot_sha256=i.snapshot_sha256,
                    fiscal_calculation_version=i.fiscal_calculation_version,
                ))
        
        # Reconstrói pagamentos
        if m.payments:
            for p in m.payments:
                s.payments.append(Payment(
                    sale_id=s.id,
                    method=PaymentMethod(p.method),
                    amount=p.amount,
                    installments=p.installments
                ))

        # Reconstrói a nota fiscal usando o novo FiscalDocument se disponível
        if getattr(m, "fiscal_documents", None):
            sorted_docs = sorted(m.fiscal_documents, key=lambda d: d.created_at, reverse=True)
            doc = sorted_docs[0]
            
            # Mapeia status do banco para os que o front espera
            status_map = {
                "authorized": "autorizada",
                "rejected": "rejeitada",
                "provider_error": "erro",
                "sefaz_unavailable": "erro",
                "manual_action_required": "erro",
                "canceled": "cancelada",
                "queued": "processando",
                "processing": "processando",
                "offline_receipt_issued": "processando",
                "contingency_required": "processando",
                "not_requested": "pendente"
            }
            mapped_status = status_map.get(doc.status, "processando")
            
            s.invoice = {
                "id": str(doc.id),
                "status": mapped_status,
                "access_key": doc.access_key,
                "number": doc.number,
                "series": doc.series,
                "xml_url": f"/api/v1/fiscal/artifacts/download?doc_id={doc.id}&type=xml" if doc.access_key else None,
                "pdf_url": f"/api/v1/fiscal/artifacts/download?doc_id={doc.id}&type=pdf" if doc.access_key else None,
                "error_message": doc.sefaz_message,
                "emitted_at": doc.authorized_at or doc.issued_at
            }
        return s


def _serialize_fiscal_tax_snapshot(snapshot: Optional[dict]) -> Optional[str]:
    if snapshot is None:
        return None
    return canonical_fiscal_snapshot_json(snapshot)

def _deserialize_fiscal_tax_snapshot(snapshot: Optional[str]) -> Optional[dict]:
    return json.loads(snapshot) if snapshot else None
# ==================================================================================
# CUSTOMER REPOSITORY
# ==================================================================================
class SQLAlchemyCustomerRepository(CustomerRepositoryInterface):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_cpf(self, market_id: uuid.UUID, cpf: str) -> Optional[Customer]:
        stmt = select(CustomerModel).where(CustomerModel.market_id == market_id, CustomerModel.cpf == cpf)
        res = await self.session.execute(stmt)
        m = res.scalars().first()
        return self._to_entity(m) if m else None

    async def get_by_id(self, customer_id: uuid.UUID) -> Optional[Customer]:
        stmt = select(CustomerModel).where(CustomerModel.id == customer_id)
        res = await self.session.execute(stmt)
        m = res.scalars().first()
        return self._to_entity(m) if m else None

    async def list_by_market(self, market_id: uuid.UUID, limit: int = 100, offset: int = 0) -> List[Customer]:
        stmt = select(CustomerModel).where(CustomerModel.market_id == market_id).limit(limit).offset(offset)
        res = await self.session.execute(stmt)
        return [self._to_entity(m) for m in res.scalars().all()]

    async def save(self, customer: Customer, commit: bool = True) -> Customer:
        model = await self._get_model_by_id(customer.id)
        if not model:
            model = CustomerModel(id=customer.id, market_id=customer.market_id)
            self.session.add(model)
        
        model.name = customer.name
        model.cpf = customer.cpf.value if customer.cpf else None
        model.phone = customer.phone
        model.credit_limit = customer.credit_limit
        model.current_debt = customer.current_debt
        model.status = customer.status.value
        model.updated_at = customer.updated_at

        # --- FIX: Processa Pending Ledger independente do commit flag ---
        # Isso garante que a movimentação seja adicionada na sessão mesmo que 
        # o commit ocorra externamente (ex: no SalesService)
        for entry in customer._pending_ledger:
            ledger_model = CustomerLedgerModel(
                id=entry.id,
                customer_id=customer.id,
                amount=entry.amount,
                type=entry.type.value,
                description=entry.description,
                sale_id=entry.sale_id,
                created_at=entry.created_at
            )
            self.session.add(ledger_model)
        
        # Limpa a lista para evitar duplicação em chamadas subsequentes
        customer._pending_ledger.clear()
        
        if commit:
            await self.session.commit()
            await self.session.refresh(model)
        
        return customer

    async def get_total_debt(self, market_id: uuid.UUID) -> float:
        stmt = select(func.sum(CustomerModel.current_debt)).where(
            CustomerModel.market_id == market_id,
            CustomerModel.status == 'ativo'
        )
        return (await self.session.execute(stmt)).scalar() or Decimal("0.00")

    async def get_ledger(self, customer_id: uuid.UUID, limit: int = 20) -> List[CustomerLedger]:
        stmt = select(CustomerLedgerModel).where(CustomerLedgerModel.customer_id == customer_id).order_by(desc(CustomerLedgerModel.created_at)).limit(limit)
        res = await self.session.execute(stmt)
        
        output = []
        for m in res.scalars().all():
            l = CustomerLedger(
                customer_id=m.customer_id,
                amount=m.amount,
                type=LedgerType(m.type),
                description=m.description,
                sale_id=m.sale_id
            )
            l.id = m.id
            l.created_at = m.created_at
            output.append(l)
        return output

    async def _get_model_by_id(self, cid: uuid.UUID) -> Optional[CustomerModel]:
        res = await self.session.execute(select(CustomerModel).where(CustomerModel.id == cid))
        return res.scalars().first()

    def _to_entity(self, m: CustomerModel) -> Customer:
        c = Customer(
            market_id=m.market_id,
            name=m.name,
            cpf=CPF(m.cpf) if m.cpf else None,
            phone=m.phone,
            credit_limit=m.credit_limit,
            current_debt=m.current_debt,
            status=CustomerStatus(m.status)
        )
        c.id = m.id
        c.created_at = m.created_at
        c.updated_at = m.updated_at
        return c

# ==================================================================================
# TICKET REPOSITORY
# ==================================================================================
class SQLAlchemyTicketRepository(TicketRepositoryInterface):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(self, ticket: Ticket, commit: bool = True) -> Ticket:
        model = await self.session.get(TicketModel, ticket.id)
        if not model:
            model = TicketModel(id=ticket.id)
            self.session.add(model)
        
        model.requester_id = ticket.requester_id
        model.market_id = ticket.market_id
        model.subject = ticket.subject
        model.status = ticket.status.value
        model.priority = ticket.priority.value
        model.assigned_to_id = ticket.assigned_to_id
        
        for msg in ticket.messages:
            msg_model = await self.session.get(TicketMessageModel, msg.id)
            if not msg_model:
                msg_model = TicketMessageModel(
                    id=msg.id,
                    ticket_id=ticket.id,
                    sender_id=msg.sender_id,
                    content=msg.content,
                    is_internal=msg.is_internal,
                    created_at=msg.created_at
                )
                self.session.add(msg_model)

        if commit:
            await self.session.commit()
        return ticket

    async def list_all(self, status: Optional[TicketStatus] = None) -> List[Ticket]:
        query = select(TicketModel).options(selectinload(TicketModel.messages))
        if status:
            query = query.where(TicketModel.status == status.value)
        query = query.order_by(desc(TicketModel.created_at))
        
        result = await self.session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def list_all_enriched(self) -> List[Tuple[Ticket, User]]:
        """Retorna uma lista de tuplas (Ticket, User) para o admin."""
        # Usa selectinload para mensagens e join para usuário (requester)
        # O join garante que pegamos o usuário.
        # Devido ao select(), retornará Row(TicketModel, UserModel)
        query = select(TicketModel, UserModel)\
            .join(UserModel, TicketModel.requester_id == UserModel.id)\
            .options(selectinload(TicketModel.messages))\
            .order_by(desc(TicketModel.created_at))
            
        result = await self.session.execute(query)
        
        # Mapeia para Entidades de Domínio
        # Como não temos acesso fácil ao user_repo aqui, mapeamos o usuário manualmente (simplificado)
        # ou instanciamos um user repo temporário/helper se necessário.
        # Mas para evitar dependência circular, faremos um mapeamento direto.
        
        enriched_list = []
        for row in result.all():
            ticket_model = row[0]
            user_model = row[1]
            
            ticket_entity = self._to_entity(ticket_model)
            user_entity = self._user_model_to_entity(user_model)
            
            enriched_list.append((ticket_entity, user_entity))
            
        return enriched_list

    async def list_by_user(self, user_id: uuid.UUID) -> List[Ticket]:
        query = select(TicketModel).options(selectinload(TicketModel.messages)).where(
            TicketModel.requester_id == user_id
        ).order_by(desc(TicketModel.created_at))
        result = await self.session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def get_by_id(self, ticket_id: uuid.UUID) -> Optional[Ticket]:
        query = select(TicketModel).options(selectinload(TicketModel.messages)).where(TicketModel.id == ticket_id)
        result = await self.session.execute(query)
        model = result.scalars().first()
        return self._to_entity(model) if model else None

    def _to_entity(self, m: TicketModel) -> Ticket:
        t = Ticket(
            requester_id=m.requester_id,
            market_id=m.market_id,
            subject=m.subject,
            status=TicketStatus(m.status),
            priority=TicketPriority(m.priority),
            assigned_to_id=m.assigned_to_id
        )
        t.id = m.id
        t.created_at = m.created_at
        
        t.messages = [
            TicketMessage(
                ticket_id=msg.ticket_id,
                sender_id=msg.sender_id,
                content=msg.content,
                is_internal=msg.is_internal,
                id=msg.id,
                created_at=msg.created_at
            ) for msg in m.messages
        ]
        return t
    
    def _user_model_to_entity(self, m: UserModel) -> User:
        # Helper para evitar dependência circular com UserRepository
        u = User(
            name=m.name,
            email=Email(m.email),
            cpf=CPF(m.cpf),
            password_hash=m.password_hash,
            role=UserRole(m.role),
            is_active=m.is_active,
            plan_id=m.plan_id,
            plan_expiration=m.plan_expiration
        )
        u.id = m.id
        u.created_at = m.created_at
        return u

# ==================================================================================
# FINANCIAL TRANSACTION REPOSITORY
# ==================================================================================
class SQLAlchemyFinancialTransactionRepository(FinancialTransactionRepositoryInterface):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(self, transaction: FinancialTransaction, commit: bool = True) -> FinancialTransaction:
        model = await self.session.get(FinancialTransactionModel, transaction.id)
        if not model:
            model = FinancialTransactionModel(id=transaction.id)
            self.session.add(model)
        
        model.market_id = transaction.market_id
        model.description = transaction.description
        model.amount = transaction.amount
        model.type = transaction.type.value
        model.due_date = transaction.due_date
        model.paid_at = transaction.paid_at
        model.category = transaction.category
        model.sale_id = transaction.sale_id
        model.customer_id = transaction.customer_id
        
        if commit:
            await self.session.commit()
        return transaction
    
    async def get_by_id(self, transaction_id: uuid.UUID) -> Optional[FinancialTransaction]:
        model = await self.session.get(FinancialTransactionModel, transaction_id)
        if not model: return None
        t = FinancialTransaction(
            market_id=model.market_id,
            description=model.description,
            amount=model.amount,
            type=TransactionType(model.type),
            due_date=model.due_date,
            paid_at=model.paid_at,
            category=model.category
        )
        t.id = model.id
        return t

    async def list_by_market(self, market_id: uuid.UUID, start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[FinancialTransaction]:
        query = select(FinancialTransactionModel).where(FinancialTransactionModel.market_id == market_id)
        
        if start_date:
            try:
                # Assume formato ISO YYYY-MM-DD ou YYYY-MM-DDTHH:MM:SS
                # Se vier apenas data, adiciona tempo min
                if "T" not in start_date:
                    dt_start = datetime.fromisoformat(start_date).replace(hour=0, minute=0, second=0, microsecond=0)
                else:
                    dt_start = datetime.fromisoformat(start_date)
                query = query.where(FinancialTransactionModel.due_date >= dt_start)
            except ValueError:
                pass 
        
        if end_date:
            try:
                if "T" not in end_date:
                    dt_end = datetime.fromisoformat(end_date).replace(hour=23, minute=59, second=59, microsecond=999999)
                else:
                    dt_end = datetime.fromisoformat(end_date)
                query = query.where(FinancialTransactionModel.due_date <= dt_end)
            except ValueError:
                pass

        query = query.order_by(desc(FinancialTransactionModel.due_date))
        
        result = await self.session.execute(query)
        models = result.scalars().all()
        
        transactions = []
        for model in models:
            t = FinancialTransaction(
                market_id=model.market_id,
                description=model.description,
                amount=model.amount,
                type=TransactionType(model.type),
                due_date=model.due_date,
                paid_at=model.paid_at,
                category=model.category
            )
            t.id = model.id
            transactions.append(t)
        return transactions

    async def get_summary_by_month(self, market_id: uuid.UUID, year: int, month: int) -> List[FinancialTransaction]:
        """
        Retorna as transações financeiras de um mês/ano específico para cálculo de dashboard.
        """
        query = select(FinancialTransactionModel).where(
            FinancialTransactionModel.market_id == market_id,
            extract('year', FinancialTransactionModel.due_date) == year,
            extract('month', FinancialTransactionModel.due_date) == month
        )
        
        result = await self.session.execute(query)
        models = result.scalars().all()
        
        transactions = []
        for model in models:
            t = FinancialTransaction(
                market_id=model.market_id,
                description=model.description,
                amount=model.amount,
                type=TransactionType(model.type),
                due_date=model.due_date,
                paid_at=model.paid_at,
                category=model.category
            )
            t.id = model.id
            transactions.append(t)
        return transactions

    async def list_recent(self, market_id: uuid.UUID, limit: int = 50) -> List[FinancialTransaction]:
        query = select(FinancialTransactionModel).where(
            FinancialTransactionModel.market_id == market_id
        ).order_by(desc(FinancialTransactionModel.created_at)).limit(limit)
        
        result = await self.session.execute(query)
        models = result.scalars().all()
        
        transactions = []
        for model in models:
            t = FinancialTransaction(
                market_id=model.market_id,
                description=model.description,
                amount=model.amount,
                type=TransactionType(model.type),
                due_date=model.due_date,
                paid_at=model.paid_at,
                category=model.category
            )
            t.id = model.id
            t.created_at = model.created_at # Ensure created_at is populated
            transactions.append(t)
        return transactions

# ==================================================================================
# FISCAL REPOSITORY
# ==================================================================================
class SQLAlchemyFiscalRepository(FiscalRepositoryInterface):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_config(self, config: FiscalConfig, commit: bool = True) -> FiscalConfig:
        model = await self.session.get(FiscalConfigModel, config.id)
        if not model:
            model = FiscalConfigModel(id=config.id)
            self.session.add(model)
        
        model.market_id = config.market_id
        model.certificate_path = config.certificate_path
        model.certificate_password = config.certificate_password
        model.csc_token = config.csc_token
        model.csc_id = config.csc_id
        model.environment = config.environment.value
        model.default_ncm = config.default_ncm
        model.default_cfop = config.default_cfop
        
        if commit:
            await self.session.commit()
        return config

    async def get_config(self, market_id: uuid.UUID) -> Optional[FiscalConfig]:
        query = select(FiscalConfigModel).where(FiscalConfigModel.market_id == market_id)
        res = await self.session.execute(query)
        m = res.scalars().first()
        return self._to_entity_config(m) if m else None

    async def get_invoice_by_sale(self, sale_id: uuid.UUID) -> Optional[Invoice]:
        query = select(InvoiceModel).where(InvoiceModel.sale_id == sale_id)
        res = await self.session.execute(query)
        m = res.scalars().first()
        return self._to_entity_invoice(m) if m else None

    async def save_invoice(self, invoice: Invoice, commit: bool = True) -> Invoice:
        model = await self.session.get(InvoiceModel, invoice.id)
        if not model:
            model = InvoiceModel(id=invoice.id)
            self.session.add(model)
        
        model.market_id = invoice.market_id
        model.sale_id = invoice.sale_id
        model.status = invoice.status.value
        model.access_key = invoice.access_key
        model.protocol = invoice.protocol
        model.series = invoice.series
        model.number = invoice.number
        model.xml_url = invoice.xml_url
        model.pdf_url = invoice.pdf_url
        model.error_message = invoice.error_message
        model.emitted_at = invoice.emitted_at

        if commit:
            await self.session.commit()
        return invoice

    def _to_entity_config(self, m: FiscalConfigModel) -> FiscalConfig:
        c = FiscalConfig(
            market_id=m.market_id,
            certificate_path=m.certificate_path,
            certificate_password=m.certificate_password,
            csc_token=m.csc_token,
            csc_id=m.csc_id,
            environment=FiscalEnvironment(m.environment),
            default_ncm=m.default_ncm,
            default_cfop=m.default_cfop
        )
        c.id = m.id
        return c

    def _to_entity_invoice(self, m: InvoiceModel) -> Invoice:
        inv = Invoice(
            market_id=m.market_id,
            sale_id=m.sale_id,
            status=InvoiceStatus(m.status),
            access_key=m.access_key,
            protocol=m.protocol,
            series=m.series,
            number=m.number,
            xml_url=m.xml_url,
            pdf_url=m.pdf_url,
            error_message=m.error_message,
            emitted_at=m.emitted_at
        )
        inv.id = m.id
        return inv
