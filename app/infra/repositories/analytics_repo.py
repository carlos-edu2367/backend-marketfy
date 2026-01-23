from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, extract, case, cast, Date, Integer, and_, literal_column, union_all
from datetime import datetime, timedelta, date
from typing import List, Dict, Optional
import uuid
from decimal import Decimal

from infra.database.models import (
    SaleModel, SaleItemModel, PaymentModel, ProductModel, 
    UserModel, MarketModel, PlanModel, FinancialTransactionModel
)

class AnalyticsRepository:
    """
    Repositório especializado em OLAP (Online Analytical Processing)
    para extração de métricas de negócio.
    """
    def __init__(self, session: AsyncSession):
        self.session = session

    # --- NOVO: MÉTODOS PARA DASHBOARD FINANCEIRO UNIFICADO ---

    async def get_financial_consolidated(self, market_id: uuid.UUID, start_date: datetime, end_date: datetime) -> Dict:
        """
        Retorna Receitas (Vendas + Entradas) e Despesas (Saídas) consolidadas no período.
        """
        # 1. Total Vendas (Concluídas)
        sales_query = select(func.sum(SaleModel.total_amount)).where(
            SaleModel.market_id == market_id,
            SaleModel.status == 'concluida',
            SaleModel.created_at >= start_date,
            SaleModel.created_at <= end_date
        )
        sales_total = (await self.session.execute(sales_query)).scalar() or Decimal("0.00")

        # 2. Transações Manuais (Receitas e Despesas)
        trans_query = select(
            FinancialTransactionModel.type,
            func.sum(FinancialTransactionModel.amount)
        ).where(
            FinancialTransactionModel.market_id == market_id,
            FinancialTransactionModel.due_date >= start_date,
            FinancialTransactionModel.due_date <= end_date
        ).group_by(FinancialTransactionModel.type)
        
        trans_res = await self.session.execute(trans_query)
        manual_income = Decimal("0.00")
        manual_expense = Decimal("0.00")
        
        for type_, amount in trans_res.all():
            # Verifica o enum ou string
            t_str = type_.value if hasattr(type_, 'value') else str(type_)
            if t_str in ['receita', 'entrada']:
                manual_income += amount
            elif t_str in ['despesa', 'saida']:
                manual_expense += amount

        return {
            "revenue": sales_total + manual_income,
            "expenses": manual_expense
        }

    async def get_financial_evolution(self, market_id: uuid.UUID, start_date: datetime, end_date: datetime, group_by: str) -> List[Dict]:
        """
        Gera dados para gráfico de área (Receita vs Despesa) agrupados por dia, semana ou mês.
        Usa UNION ALL para juntar Sales e FinancialTransactions.
        """
        
        # Define o extrator de data baseado no agrupamento
        # group_by: 'day', 'week', 'month'
        if group_by == 'week':
            date_col_sales = func.to_char(SaleModel.created_at, 'YYYY-IW') # ISO Week
            date_col_trans = func.to_char(FinancialTransactionModel.due_date, 'YYYY-IW')
        elif group_by == 'month':
            date_col_sales = func.to_char(SaleModel.created_at, 'YYYY-MM')
            date_col_trans = func.to_char(FinancialTransactionModel.due_date, 'YYYY-MM')
        else: # day (default for month view)
            date_col_sales = func.to_char(SaleModel.created_at, 'YYYY-MM-DD')
            date_col_trans = func.to_char(FinancialTransactionModel.due_date, 'YYYY-MM-DD')

        # Query Vendas (Conta como Receita)
        q_sales = select(
            date_col_sales.label('period'),
            func.sum(SaleModel.total_amount).label('revenue'),
            literal_column("0").label('expense')
        ).where(
            SaleModel.market_id == market_id,
            SaleModel.status == 'concluida',
            SaleModel.created_at >= start_date,
            SaleModel.created_at <= end_date
        ).group_by('period')

        # Query Transações (Receita e Despesa)
        # Precisamos separar tipos no SUM
        q_trans = select(
            date_col_trans.label('period'),
            func.sum(case((FinancialTransactionModel.type.in_(['receita', 'entrada']), FinancialTransactionModel.amount), else_=0)).label('revenue'),
            func.sum(case((FinancialTransactionModel.type.in_(['despesa', 'saida']), FinancialTransactionModel.amount), else_=0)).label('expense')
        ).where(
            FinancialTransactionModel.market_id == market_id,
            FinancialTransactionModel.due_date >= start_date,
            FinancialTransactionModel.due_date <= end_date
        ).group_by('period')

        # Union e Soma Final
        union_q = union_all(q_sales, q_trans).alias('combined')
        
        final_query = select(
            union_q.c.period,
            func.sum(union_q.c.revenue).label('total_revenue'),
            func.sum(union_q.c.expense).label('total_expense')
        ).group_by(union_q.c.period).order_by(union_q.c.period)

        result = await self.session.execute(final_query)
        
        output = []
        for row in result.all():
            output.append({
                "label": row.period,
                "receitas": row.total_revenue or Decimal("0.00"),
                "despesas": row.total_expense or Decimal("0.00")
            })
        return output

    # --- MÉTRICAS DE MERCADO (TENANT) ---

    async def get_ticket_metrics(self, market_id: uuid.UUID) -> Dict:
        """Retorna Ticket Médio e Total Vendido Hoje."""
        today = datetime.utcnow().date()
        
        # Vendas Hoje
        query_today = select(
            func.sum(SaleModel.total_amount).label('total'),
            func.count(SaleModel.id).label('count')
        ).where(
            SaleModel.market_id == market_id,
            cast(SaleModel.created_at, Date) == today,
            SaleModel.status == 'concluida'
        )
        res_today = await self.session.execute(query_today)
        row_today = res_today.first()
        total_today = row_today.total or 0
        count_today = row_today.count or 0

        # Total Mês (para ticket médio mais robusto)
        start_month = today.replace(day=1)
        query_month = select(
            func.sum(SaleModel.total_amount).label('total'),
            func.count(SaleModel.id).label('count')
        ).where(
            SaleModel.market_id == market_id,
            SaleModel.created_at >= start_month,
            SaleModel.status == 'concluida'
        )
        res_month = await self.session.execute(query_month)
        row_month = res_month.first()
        total_month = row_month.total or 0
        count_month = row_month.count or 0

        ticket_avg = (total_month / count_month) if count_month > 0 else 0

        return {
            "total_revenue_today": total_today,
            "sales_count_today": count_today,
            "total_revenue_month": total_month,
            "ticket_average": ticket_avg
        }

    async def get_sales_by_hour_today(self, market_id: uuid.UUID) -> List[Dict]:
        """Mapa de calor de vendas por hora no dia atual."""
        today = datetime.utcnow().date()
        
        query = select(
            extract('hour', SaleModel.created_at).label('hour'),
            func.sum(SaleModel.total_amount).label('total'),
            func.count(SaleModel.id).label('count')
        ).where(
            SaleModel.market_id == market_id,
            cast(SaleModel.created_at, Date) == today,
            SaleModel.status == 'concluida'
        ).group_by('hour').order_by('hour')

        result = await self.session.execute(query)
        return [{"hour": int(r.hour), "total": r.total, "count": r.count} for r in result.all()]

    async def get_sales_evolution_daily(self, market_id: uuid.UUID, year: int, month: int) -> List[Dict]:
        """Evolução dia-a-dia dentro de um mês."""
        query = select(
            extract('day', SaleModel.created_at).label('day'),
            func.sum(SaleModel.total_amount).label('total')
        ).where(
            SaleModel.market_id == market_id,
            extract('year', SaleModel.created_at) == year,
            extract('month', SaleModel.created_at) == month,
            SaleModel.status == 'concluida'
        ).group_by('day').order_by('day')

        result = await self.session.execute(query)
        return [{"label": f"{int(r.day):02d}/{month:02d}", "total": r.total} for r in result.all()]

    async def get_sales_evolution_monthly(self, market_id: uuid.UUID, year: int) -> List[Dict]:
        """Evolução mês-a-mês dentro de um ano."""
        query = select(
            extract('month', SaleModel.created_at).label('month'),
            func.sum(SaleModel.total_amount).label('total')
        ).where(
            SaleModel.market_id == market_id,
            extract('year', SaleModel.created_at) == year,
            SaleModel.status == 'concluida'
        ).group_by('month').order_by('month')

        result = await self.session.execute(query)
        # Lista de nomes de meses (simplificada)
        months = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
        return [{"label": months[int(r.month)-1], "total": r.total} for r in result.all()]

    async def get_payment_method_stats(self, market_id: uuid.UUID, year: int = None, month: int = None) -> List[Dict]:
        """
        Totais por método de pagamento.
        Se year/month fornecidos, filtra pelo período. Caso contrário, pega tudo (ou mês atual).
        """
        query = select(
            PaymentModel.method,
            func.sum(PaymentModel.amount).label('total'),
            func.count(PaymentModel.id).label('count')
        ).join(SaleModel, PaymentModel.sale_id == SaleModel.id).where(
            SaleModel.market_id == market_id,
            SaleModel.status == 'concluida'
        )

        if year and month:
            query = query.where(
                extract('year', SaleModel.created_at) == year,
                extract('month', SaleModel.created_at) == month
            )
        
        query = query.group_by(PaymentModel.method).order_by(desc('total'))

        result = await self.session.execute(query)
        return [{"method": r.method, "total": r.total, "count": r.count} for r in result.all()]

    async def get_top_products(self, market_id: uuid.UUID, limit: int = 5, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> List[Dict]:
        """
        Produtos mais vendidos (Curva ABC simplificada).
        Suporta filtro por período (ex: Hoje).
        """
        query = select(
            SaleItemModel.product_name_snapshot.label('name'),
            func.sum(SaleItemModel.quantity).label('qty'),
            func.sum(SaleItemModel.total).label('revenue')
        ).join(SaleModel, SaleItemModel.sale_id == SaleModel.id).where(
            SaleModel.market_id == market_id,
            SaleModel.status == 'concluida'
        )

        # Filtro de data opcional (ex: Top Products Hoje)
        if start_date and end_date:
            query = query.where(
                SaleModel.created_at >= start_date,
                SaleModel.created_at <= end_date
            )

        query = query.group_by(SaleItemModel.product_name_snapshot).order_by(desc('revenue')).limit(limit)

        result = await self.session.execute(query)
        return [
            {"product_name": r.name, "quantity_sold": r.qty, "total_revenue": r.revenue} 
            for r in result.all()
        ]

    # --- MÉTRICAS DO SAAS (ADMIN) ---

    async def get_saas_metrics(self) -> Dict:
        """Retorna métricas globais para o Admin do SaaS."""
        
        # MRR (Monthly Recurring Revenue)
        mrr_query = select(func.sum(PlanModel.price_monthly))\
            .join(UserModel, UserModel.plan_id == PlanModel.id)\
            .where(UserModel.is_active == True)
        mrr = (await self.session.execute(mrr_query)).scalar() or 0

        # Total Users
        users_query = select(func.count(UserModel.id))
        total_users = (await self.session.execute(users_query)).scalar() or 0

        # Active Markets
        markets_query = select(func.count(MarketModel.id)).where(MarketModel.is_active == True)
        active_markets = (await self.session.execute(markets_query)).scalar() or 0

        return {
            "mrr": mrr,
            "total_users": total_users,
            "active_markets": active_markets
        }