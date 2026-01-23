from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text, case, desc
from datetime import datetime, timedelta
from infra.database.models import UserModel, PlanModel, TicketModel, MarketModel
from typing import List, Tuple

class AdminStatsRepository:
    """
    Repositório especializado em consultas analíticas para o Admin do SaaS.
    """
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_general_stats(self):
        """Retorna contagens totais, MRR e Tickets Abertos."""
        
        # 1. Total de Usuários e Ativos
        users_query = select(
            func.count(UserModel.id).label("total"),
            func.sum(case((UserModel.is_active == True, 1), else_=0)).label("active")
        )
        users_res = await self.session.execute(users_query)
        users_row = users_res.first()
        total_users = users_row.total or 0
        active_users = users_row.active or 0

        # 2. Total de Mercados
        markets_query = select(func.count(MarketModel.id))
        markets_res = await self.session.execute(markets_query)
        total_markets = markets_res.scalar() or 0

        # 3. MRR (Receita Mensal Recorrente)
        # Soma price_monthly de todos os planos vinculados a usuários ativos
        mrr_query = select(func.sum(PlanModel.price_monthly))\
            .join(UserModel, UserModel.plan_id == PlanModel.id)\
            .where(UserModel.is_active == True)
        
        mrr_res = await self.session.execute(mrr_query)
        mrr = mrr_res.scalar() or 0

        # 4. Tickets Abertos
        tickets_query = select(func.count(TicketModel.id)).where(TicketModel.status == 'aberto')
        tickets_res = await self.session.execute(tickets_query)
        open_tickets = tickets_res.scalar() or 0

        return {
            "total_users": total_users,
            "active_users": active_users,
            "total_markets": total_markets,
            "mrr": float(mrr),
            "open_tickets": open_tickets
        }

    async def get_expiring_plans(self, days_threshold: int = 7):
        """Lista usuários cujos planos expiram nos próximos X dias."""
        limit_date = datetime.utcnow() + timedelta(days=days_threshold)
        
        # Seleciona User e Nome do Plano
        query = select(UserModel, PlanModel.name.label("plan_name"))\
            .join(PlanModel, UserModel.plan_id == PlanModel.id)\
            .where(
                UserModel.plan_expiration != None,
                UserModel.plan_expiration <= limit_date,
                UserModel.plan_expiration >= datetime.utcnow()
            ).order_by(UserModel.plan_expiration.asc())
            
        result = await self.session.execute(query)
        return result.all() # Retorna lista de Row(UserModel, plan_name)

    async def list_users_enriched(self):
        """
        Lista todos os usuários com detalhes do plano e contagem de lojas.
        Join: User -> Plan (Left Join)
        Join: User -> Market (Left Join + Count)
        """
        query = select(
            UserModel, 
            PlanModel.name.label("plan_name"),
            func.count(MarketModel.id).label("markets_count")
        ).outerjoin(PlanModel, UserModel.plan_id == PlanModel.id)\
         .outerjoin(MarketModel, MarketModel.owner_id == UserModel.id)\
         .group_by(UserModel.id, PlanModel.name)\
         .order_by(desc(UserModel.created_at))
        
        result = await self.session.execute(query)
        return result.all() # Retorna Row(UserModel, plan_name, markets_count)